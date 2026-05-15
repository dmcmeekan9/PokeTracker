from __future__ import annotations

import os
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poketracker.checkout.target_storage_state import decode_storage_state_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest

TARGET_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CLICK_STEP_DEADLINES = {
    "add_to_cart": 12,
    "cart_or_checkout": 5,
    "checkout": 8,
    "place_order": 10,
    "quantity_increment": 5,
}
DEFAULT_CLICK_DEADLINE_SECONDS = 10
OPTIONAL_CLICK_DEADLINE_SECONDS = 4
CLICK_TIMEOUT_MS = 2500
CLICK_LOAD_STATE_TIMEOUT_MS = 3000


@dataclass(frozen=True)
class TargetCheckoutResult:
    status: str
    order_id: str | None
    message: str
    quantity: int
    storage_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class TargetSessionRefreshResult:
    status: str
    message: str
    storage_state: dict[str, Any]


def purchase_target_item(
    request: PurchaseRequest,
    profile: dict[str, Any],
    target_session_json: str | None,
    *,
    verify_only: bool = False,
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
        browser = _launch_target_browser(playwright)
        context = _new_target_context(browser, storage_state)
        page = context.new_page()
        try:
            _goto_target_page(page, request.url)
            _dismiss_target_overlays(page)
            _stop_on_intervention(_page_content(page))
            if not _click_first(page, [r"add to cart", r"add for shipping", r"ship it"], "add_to_cart", optional=True):
                if not _page_indicates_cart_has_item(_page_content(page)):
                    raise CheckoutWebhookError(
                        409,
                        "target_add_to_cart_not_found",
                        "Target checkout could not find the add_to_cart control",
                    )
            _click_first(page, [r"view cart", r"checkout", r"check\s*out", r"cart"], "cart_or_checkout", optional=True)
            if "cart" not in page.url and "checkout" not in page.url:
                _goto_target_page(page, "https://www.target.com/cart")
            _stop_on_intervention(_page_content(page))
            _select_standard_shipping(page)
            actual_quantity = _set_target_quantity(page, request.quantity)
            _click_first(page, [r"checkout", r"check\s*out", r"sign in to check out"], "checkout", optional=True)
            _wait_for_checkout_ready(page, profile)
            _stop_on_intervention(_page_content(page))

            if verify_only:
                _verify_click_candidate_present(page, [r"place your order", r"place order", r"submit order"], "place_order")
                return TargetCheckoutResult(
                    status="ready_to_place_order",
                    order_id=None,
                    message="Target checkout reached the final place-order control; no order was placed",
                    quantity=actual_quantity,
                    storage_state=context.storage_state(),
                )

            _verify_checkout_profile_visible(_page_content(page), profile, page)

            if not place_order_enabled:
                raise CheckoutWebhookError(
                    409,
                    "place_order_disabled",
                    "TARGET_PLACE_ORDER_ENABLED is not true; cart/checkout was prepared but no order was placed",
                )

            _click_first(page, [r"place your order", r"place order", r"submit order"], "place_order")
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            confirmation_html = _page_content(page)
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
                storage_state=context.storage_state(),
            )
        except PlaywrightTimeoutError as exc:
            raise CheckoutWebhookError(504, "target_timeout", f"Target checkout timed out: {exc}") from exc
        finally:
            context.close()
            browser.close()


def refresh_target_session(
    target_session_json: str | None,
    verify_url: str | None = None,
) -> TargetSessionRefreshResult:
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

    with sync_playwright() as playwright:
        browser = _launch_target_browser(playwright)
        context = _new_target_context(browser, storage_state)
        page = context.new_page()
        try:
            _goto_target_page(page, "https://www.target.com/account")
            _dismiss_target_overlays(page)
            _stop_on_intervention(_page_content(page))
            if verify_url:
                _goto_target_page(page, verify_url)
                _dismiss_target_overlays(page)
                _stop_on_intervention(_page_content(page))
            refreshed_state = context.storage_state()
            message = "Target session refreshed in AWS"
            if verify_url:
                message = "Target session refreshed in AWS after Target preflight verification"
            return TargetSessionRefreshResult(
                status="refreshed",
                message=message,
                storage_state=refreshed_state,
            )
        except PlaywrightTimeoutError as exc:
            raise CheckoutWebhookError(504, "target_timeout", f"Target session refresh timed out: {exc}") from exc
        finally:
            context.close()
            browser.close()


def _launch_target_browser(playwright: Any) -> Any:
    return playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def _new_target_context(browser: Any, storage_state: dict[str, Any]) -> Any:
    context = browser.new_context(
        storage_state=storage_state,
        viewport={"width": 1365, "height": 900},
        user_agent=TARGET_USER_AGENT,
        locale="en-US",
        timezone_id="America/Chicago",
        geolocation={"latitude": 41.7318, "longitude": -93.6001},
        permissions=["geolocation"],
    )
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        """
    )
    return context


def _goto_target_page(page: Any, url: str, timeout: int = 15000) -> None:
    page.goto(url, wait_until="commit", timeout=timeout)
    page.wait_for_timeout(750)


def _click_first(page: Any, labels: list[str], step: str, optional: bool = False) -> bool:
    deadline = time.monotonic() + _click_deadline_seconds(step, optional)
    while time.monotonic() < deadline:
        _dismiss_target_overlays(page)
        for candidate in _click_candidates(page, labels, step):
            try:
                candidate.first.click(timeout=CLICK_TIMEOUT_MS)
                page.wait_for_load_state("domcontentloaded", timeout=CLICK_LOAD_STATE_TIMEOUT_MS)
                return True
            except Exception:
                continue
        _stop_on_intervention(_page_content(page))
        page.wait_for_timeout(500)
    if optional:
        return False
    _write_debug_artifacts(page, step)
    raise CheckoutWebhookError(409, f"target_{step}_not_found", f"Target checkout could not find the {step} control")


def _verify_click_candidate_present(page: Any, labels: list[str], step: str) -> None:
    deadline = time.monotonic() + _click_deadline_seconds(step, optional=False)
    while time.monotonic() < deadline:
        _dismiss_target_overlays(page)
        for candidate in _click_candidates(page, labels, step):
            try:
                candidate.first.wait_for(state="visible", timeout=1000)
                return
            except Exception:
                continue
        _stop_on_intervention(_page_content(page))
        page.wait_for_timeout(500)
    _write_debug_artifacts(page, step)
    raise CheckoutWebhookError(409, f"target_{step}_not_found", f"Target checkout could not find the {step} control")


def _click_deadline_seconds(step: str, optional: bool) -> int:
    if step in CLICK_STEP_DEADLINES:
        return CLICK_STEP_DEADLINES[step]
    if optional:
        return OPTIONAL_CLICK_DEADLINE_SECONDS
    return CLICK_STEP_DEADLINES.get(step, DEFAULT_CLICK_DEADLINE_SECONDS)


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


def _dismiss_target_overlays(page: Any) -> None:
    for label in [r"continue shopping", r"close"]:
        pattern = re.compile(label, re.IGNORECASE)
        for candidate in [page.get_by_role("button", name=pattern), page.get_by_text(pattern)]:
            try:
                candidate.first.click(timeout=1000)
                page.wait_for_load_state("domcontentloaded", timeout=CLICK_LOAD_STATE_TIMEOUT_MS)
                page.wait_for_timeout(250)
                return
            except Exception:
                continue


def _select_standard_shipping(page: Any) -> None:
    locator = page.locator('input[type="radio"][id$="-shipping"][value="STANDARD"]')
    try:
        count = min(locator.count(), 10)
    except Exception:
        return
    for index in range(count):
        candidate = locator.nth(index)
        try:
            if candidate.is_checked(timeout=1000):
                continue
            candidate.check(timeout=5000, force=True)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(1000)
        except Exception:
            continue


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
    ready_to_order = bool(re.search(r"place\s+(?:your\s+)?order|submit\s+order", normalized))
    interventions = [
        ("captcha", r"\bcaptcha\b"),
        ("captcha", r"verify you(?:'| a)?re (?:a )?human"),
        ("captcha", r"not a robot"),
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
    ]
    for status, pattern in interventions:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise CheckoutWebhookError(409, status, f"Target checkout requires intervention: {status}")

    hard_payment_interventions = [
        r"enter (?:the )?(?:card )?security code",
        r"\bcvv\b",
        r"\bcvc\b",
        r"payment (?:could not|can't|cannot|was not) (?:be )?(?:authorized|processed|verified)",
        r"card (?:declined|was declined|could not be verified)",
    ]
    for pattern in hard_payment_interventions:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise CheckoutWebhookError(409, "payment_intervention", "Target checkout requires intervention: payment_intervention")

    soft_payment_interventions = [
        r"select (?:a )?payment method",
        r"select payment type",
        r"add (?:a )?payment method",
        r"update (?:your )?payment",
    ]
    if not ready_to_order:
        for pattern in soft_payment_interventions:
            if re.search(pattern, normalized, re.IGNORECASE):
                raise CheckoutWebhookError(
                    409,
                    "payment_intervention",
                    "Target checkout requires intervention: payment_intervention",
                )


def _page_indicates_cart_has_item(html: str) -> bool:
    normalized = re.sub(r"\s+", " ", html.lower())
    return bool(re.search(r"\b\d+\s+in cart\b", normalized))


def _page_content(page: Any) -> str:
    deadline = time.monotonic() + 10
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return page.content()
        except Exception as exc:
            last_exc = exc
            try:
                page.wait_for_timeout(500)
            except Exception:
                break
    raise CheckoutWebhookError(504, "target_timeout", f"Target checkout could not read page content: {last_exc}")


def _wait_for_checkout_ready(page: Any, profile: dict[str, Any]) -> None:
    postal_code = ""
    shipping = profile.get("shipping_address") if isinstance(profile, dict) else None
    if isinstance(shipping, dict):
        postal_code = str(shipping.get("postal_code", "")).strip()

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        html = _page_content(page)
        _stop_on_intervention(html)
        text = _page_text(page)
        normalized = re.sub(r"\s+", " ", text.lower())
        if postal_code and postal_code in text:
            return
        if re.search(r"place\s+(?:your\s+)?order|submit\s+order|order summary|payment", normalized):
            return
        page.wait_for_timeout(1000)


def _page_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _write_debug_artifacts(page: Any, step: str) -> None:
    output_dir = os.environ.get("TARGET_CHECKOUT_DEBUG_DIR")
    if not output_dir:
        return
    try:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        html = _page_content(page)
        buttons = []
        try:
            buttons = page.locator("button").all_inner_texts(timeout=3000)
        except Exception:
            buttons = []
        links = []
        try:
            links = page.locator("a").all_inner_texts(timeout=3000)
        except Exception:
            links = []
        metadata = {
            "step": step,
            "url": getattr(page, "url", None),
            "title": page.title(),
            "buttons": buttons,
            "links": links,
        }
        (path / f"{step}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (path / f"{step}.html").write_text(html, encoding="utf-8")
        page.screenshot(path=str(path / f"{step}.png"), full_page=True, timeout=5000)
    except Exception:
        return


def _verify_checkout_profile_visible(html: str, profile: dict[str, Any], page: Any | None = None) -> None:
    shipping = profile.get("shipping_address") if isinstance(profile, dict) else None
    if not isinstance(shipping, dict):
        raise CheckoutWebhookError(503, "profile_invalid", "checkout profile shipping_address is missing")
    postal_code = str(shipping.get("postal_code", "")).strip()
    if postal_code and postal_code not in html:
        if page is not None:
            _write_debug_artifacts(page, "shipping_profile")
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
