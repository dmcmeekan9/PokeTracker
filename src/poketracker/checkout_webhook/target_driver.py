from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

from poketracker.checkout.target_storage_state import decode_storage_state_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest


@dataclass(frozen=True)
class TargetCheckoutResult:
    status: str
    order_id: str | None
    message: str
    quantity: int


def purchase_target_item(
    request: PurchaseRequest,
    profile: dict[str, Any],
    target_session_json: str | None,
) -> TargetCheckoutResult:
    if not target_session_json:
        raise CheckoutWebhookError(503, "target_session_missing", "Target session secret is not configured")

    try:
        storage_state = decode_storage_state_secret(target_session_json)
    except ValueError as exc:
        raise CheckoutWebhookError(503, "target_session_invalid", str(exc)) from exc

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CheckoutWebhookError(503, "driver_dependency_missing", "Playwright is not installed for the checkout webhook") from exc

    place_order_enabled = os.environ.get("TARGET_PLACE_ORDER_ENABLED", "").lower() in {"1", "true", "yes"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        try:
            page.goto(request.url, wait_until="domcontentloaded", timeout=30000)
            _stop_on_intervention(page.content())
            _click_first(page, [r"add to cart", r"add for shipping", r"ship it"], "add_to_cart")
            _click_first(page, [r"view cart", r"checkout", r"cart"], "cart_or_checkout", optional=True)
            if "cart" not in page.url and "checkout" not in page.url:
                page.goto("https://www.target.com/cart", wait_until="domcontentloaded", timeout=30000)
            _stop_on_intervention(page.content())
            actual_quantity = _set_target_quantity(page, request.quantity)
            _click_first(page, [r"checkout", r"sign in to check out"], "checkout", optional=True)
            _stop_on_intervention(page.content())
            _verify_checkout_profile_visible(page.content(), profile)

            if not place_order_enabled:
                raise CheckoutWebhookError(
                    409,
                    "place_order_disabled",
                    "TARGET_PLACE_ORDER_ENABLED is not true; cart/checkout was prepared but no order was placed",
                )

            _click_first(page, [r"place your order", r"place order", r"submit order"], "place_order")
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            confirmation_html = page.content()
            _stop_on_intervention(confirmation_html)
            order_id = _extract_order_id(confirmation_html)
            message = "Target checkout completed"
            if actual_quantity != request.quantity:
                message = f"Target checkout completed with quantity {actual_quantity}; requested {request.quantity}"
            return TargetCheckoutResult(
                status="ordered",
                order_id=order_id,
                message=message,
                quantity=actual_quantity,
            )
        except PlaywrightTimeoutError as exc:
            raise CheckoutWebhookError(504, "target_timeout", f"Target checkout timed out: {exc}") from exc
        finally:
            context.close()
            browser.close()


def _click_first(page: Any, labels: list[str], step: str, optional: bool = False) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        for candidate in _click_candidates(page, labels, step):
            try:
                candidate.first.click(timeout=2000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                return
            except Exception:
                continue
        _stop_on_intervention(page.content())
        page.wait_for_timeout(500)
    if optional:
        return
    raise CheckoutWebhookError(409, f"target_{step}_not_found", f"Target checkout could not find the {step} control")


def _click_candidates(page: Any, labels: list[str], step: str) -> list[Any]:
    candidates: list[Any] = []
    if step == "add_to_cart":
        candidates.extend(
            [
                page.locator('button[data-test="shippingButton"]'),
                page.locator('button[id^="addToCartButton"]'),
            ]
        )
    for label in labels:
        pattern = re.compile(label, re.IGNORECASE)
        candidates.extend(
            [
                page.get_by_role("button", name=pattern),
                page.get_by_role("link", name=pattern),
                page.get_by_text(pattern),
            ]
        )
    return candidates


def _set_target_quantity(page: Any, quantity: int) -> int:
    if quantity == 1:
        return 1

    desired = str(quantity)
    selectors = [
        'select[aria-label*="quantity" i]',
        'select[name*="quantity" i]',
        'select[id*="quantity" i]',
        "select",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = min(locator.count(), 10)
        except Exception:
            continue
        for index in range(count):
            try:
                locator.nth(index).select_option(desired, timeout=3000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                return quantity
            except Exception:
                continue

    increment_clicks = quantity - 1
    try:
        for _ in range(increment_clicks):
            _click_first(
                page,
                [r"increase quantity", r"add one", r"increment quantity", r"quantity increase", r"^\+$"],
                "quantity_increment",
            )
        return quantity
    except CheckoutWebhookError:
        return 1


def _stop_on_intervention(html: str) -> None:
    normalized = re.sub(r"\s+", " ", html.lower())
    interventions = [
        ("captcha", r"\bcaptcha\b"),
        ("captcha", r"verify you(?:'| a)?re (?:a )?human"),
        ("captcha", r"not a robot"),
        ("captcha", r"\brecaptcha\b"),
        ("target_blocked", r"loading screen.*something went wrong.*please try again in a bit or use another device"),
        ("identity_verification", r"verify it(?:'| i)?s you"),
        ("identity_verification", r"verification code"),
        ("identity_verification", r"two[- ]factor"),
        ("identity_verification", r"multi[- ]factor"),
        ("identity_verification", r"one[- ]time (?:passcode|password|code)"),
        ("sign_in_required", r"sign in to your target account"),
        ("sign_in_required", r"sign in to check out"),
        ("sign_in_required", r"enter your password"),
        ("sign_in_required", r"password is required"),
        ("sign_in_required", r"email or mobile phone"),
        ("payment_intervention", r"enter (?:the )?(?:card )?security code"),
        ("payment_intervention", r"\bcvv\b"),
        ("payment_intervention", r"\bcvc\b"),
        ("payment_intervention", r"select (?:a )?payment method"),
        ("payment_intervention", r"add (?:a )?payment method"),
        ("payment_intervention", r"update (?:your )?payment"),
        ("payment_intervention", r"payment (?:could not|can't|cannot|was not) (?:be )?(?:authorized|processed|verified)"),
        ("payment_intervention", r"card (?:declined|was declined|could not be verified)"),
    ]
    for status, pattern in interventions:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise CheckoutWebhookError(409, status, f"Target checkout requires intervention: {status}")


def _verify_checkout_profile_visible(html: str, profile: dict[str, Any]) -> None:
    shipping = profile.get("shipping_address") if isinstance(profile, dict) else None
    if not isinstance(shipping, dict):
        raise CheckoutWebhookError(503, "profile_invalid", "checkout profile shipping_address is missing")
    postal_code = str(shipping.get("postal_code", "")).strip()
    if postal_code and postal_code not in html:
        raise CheckoutWebhookError(
            409,
            "shipping_not_confirmed",
            "Target checkout did not show the configured shipping postal code",
        )


def _extract_order_id(html: str) -> str | None:
    patterns = [
        r"order\s*(?:number|#)\s*[:#]?\s*([A-Z0-9-]{6,})",
        r"confirmation\s*(?:number|#)\s*[:#]?\s*([A-Z0-9-]{6,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    return None
