from __future__ import annotations

import os
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poketracker.checkout.target_credentials import TargetCredentials
from poketracker.checkout.target_storage_state import decode_storage_state_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest

TARGET_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CLICK_STEP_DEADLINES = {
    "add_to_cart": 12,
    "cart_or_checkout": 2,
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
    target_credentials: TargetCredentials | None = None,
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
            _ensure_target_signed_in(page, target_credentials)
            _stop_on_intervention(_page_content(page))
            _add_to_cart(page, request.url, target_credentials)
            _click_first(page, [r"view cart", r"checkout", r"check\s*out", r"cart"], "cart_or_checkout", optional=True)
            if "cart" not in page.url and "checkout" not in page.url:
                _goto_target_page(page, "https://www.target.com/cart")
            _ensure_target_signed_in(page, target_credentials)
            _stop_on_intervention(_page_content(page))
            _select_standard_shipping(page)
            actual_quantity = _set_target_quantity(page, request.quantity)
            _click_first_with_auto_login(
                page,
                [r"checkout", r"check\s*out", r"sign in to check out"],
                "checkout",
                target_credentials,
                optional=True,
            )
            _ensure_target_signed_in(page, target_credentials)
            _resume_checkout_after_sign_in(page, target_credentials)
            _wait_for_checkout_ready(page, profile, target_credentials=target_credentials)
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
    target_credentials: TargetCredentials | None = None,
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
            _ensure_target_signed_in(page, target_credentials)
            _stop_on_intervention(_page_content(page))
            if verify_url:
                _goto_target_page(page, verify_url)
                _dismiss_target_overlays(page)
                _ensure_target_signed_in(page, target_credentials)
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


def refresh_target_session_from_cdp(
    cdp_url: str,
    verify_url: str | None = None,
    target_credentials: TargetCredentials | None = None,
) -> TargetSessionRefreshResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CheckoutWebhookError(503, "driver_dependency_missing", "Playwright is not installed for the checkout webhook") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            try:
                _goto_target_page(page, "https://www.target.com/account")
                _dismiss_target_overlays(page)
                _ensure_target_signed_in(page, target_credentials)
                _stop_on_intervention(_page_content(page))
                if verify_url:
                    _goto_target_page(page, verify_url)
                    _dismiss_target_overlays(page)
                    _ensure_target_signed_in(page, target_credentials)
                    _stop_on_intervention(_page_content(page))
                message = "Target CDP browser session refreshed"
                if verify_url:
                    message = "Target CDP browser session refreshed after Target preflight verification"
                return TargetSessionRefreshResult(
                    status="refreshed",
                    message=message,
                    storage_state=context.storage_state(),
                )
            except PlaywrightTimeoutError as exc:
                raise CheckoutWebhookError(504, "target_timeout", f"Target session refresh timed out: {exc}") from exc
        finally:
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
    page.wait_for_timeout(300)


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
        page.wait_for_timeout(200)
    if optional:
        return False
    _write_debug_artifacts(page, step)
    raise CheckoutWebhookError(409, f"target_{step}_not_found", f"Target checkout could not find the {step} control")


def _click_first_with_auto_login(
    page: Any,
    labels: list[str],
    step: str,
    target_credentials: TargetCredentials | None,
    optional: bool = False,
) -> bool:
    try:
        return _click_first(page, labels, step, optional=optional)
    except CheckoutWebhookError as exc:
        if exc.status != "sign_in_required" or target_credentials is None:
            raise
        _ensure_target_signed_in(page, target_credentials)
        return True


def _add_to_cart(page: Any, url: str, target_credentials: TargetCredentials | None) -> None:
    for attempt in range(3):
        if attempt > 0:
            # Brief pause before reload to allow CDN cache to propagate fresh stock state
            page.wait_for_timeout(1500)
            _goto_target_page(page, url)
            _ensure_target_signed_in(page, target_credentials)
            _stop_on_intervention(_page_content(page))
        if _click_first(page, [r"add to cart", r"add for shipping", r"ship it"], "add_to_cart", optional=True):
            return
        if _page_indicates_cart_has_item(_page_content(page)):
            return
    raise CheckoutWebhookError(409, "target_add_to_cart_not_found", "Target checkout could not find the add_to_cart control")


def _resume_checkout_after_sign_in(page: Any, target_credentials: TargetCredentials | None) -> None:
    if "cart" not in page.url or "checkout" in page.url:
        return

    for _ in range(3):
        _ensure_target_signed_in(page, target_credentials)
        if "cart" not in page.url or "checkout" in page.url:
            return

        _click_first_with_auto_login(
            page,
            [r"checkout", r"check\s*out", r"sign in to check out"],
            "checkout",
            target_credentials,
            optional=True,
        )

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            html = _page_content(page)
            if target_credentials is not None and _page_requires_sign_in(html):
                _ensure_target_signed_in(page, target_credentials)
                break
            if "cart" not in page.url or "checkout" in page.url:
                return
            page.wait_for_timeout(500)

    if "cart" in page.url and "checkout" not in page.url:
        _goto_target_page(page, "https://www.target.com/checkout")
        _ensure_target_signed_in(page, target_credentials)


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
        page.wait_for_timeout(200)
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
                page.locator('button[data-test="fulfillmentSection_shippingButton"]'),
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


def _ensure_target_signed_in(page: Any, target_credentials: TargetCredentials | None) -> bool:
    if target_credentials is None or not _page_requires_sign_in(_page_content(page)):
        return False

    if _page_has_remembered_target_account(_page_text(page)):
        _click_first_without_intervention(page, [r"enter your password", r"use password"], optional=True)
    else:
        if not _fill_first(
            page,
            [
                'input[data-test="username"]',
                'input[data-test="email"]',
                'main input[name="username"]',
                'main input[id="username"]',
                'main input[type="email"]',
                'main input[autocomplete="username"]',
                '[role="dialog"] input[name="username"]',
                '[role="dialog"] input[id="username"]',
                '[role="dialog"] input[type="email"]',
                '[role="dialog"] input[autocomplete="username"]',
                'input[name="username"]',
                'input[id="username"]',
                'input[type="email"]',
                'input[autocomplete="username"]',
            ],
            [r"email or mobile phone", r"mobile phone", r"phone number", r"username"],
            target_credentials.username,
        ):
            raise CheckoutWebhookError(409, "sign_in_required", "Target sign-in form did not expose the username field")

        # Advance the username step: press Enter, then immediately click the
        # Continue/Sign In button. Do NOT poll for the password field first —
        # burning 12s on _fill_password before clicking Continue means the
        # button may disappear before we ever try it.
        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
        except Exception:
            pass
        _click_first_without_intervention(
            page, [r"continue", r"next", r"sign in", r"log in", r"submit"], optional=True
        )
    if not _fill_password_after_username(page, target_credentials.password):
        raise CheckoutWebhookError(409, "sign_in_required", "Target sign-in form did not expose the password field")

    if not _click_first_without_intervention(page, [r"sign in", r"log in", r"continue"], optional=False):
        raise CheckoutWebhookError(409, "sign_in_required", "Target sign-in form did not expose the sign-in button")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        html = _page_content(page)
        if not _page_requires_sign_in(html):
            return True
        if _page_requires_human_intervention(html):
            _stop_on_intervention(html)

    _write_debug_artifacts(page, "target_auto_login")
    raise CheckoutWebhookError(409, "sign_in_required", "Target auto-login did not complete")


def _fill_password_after_username(page: Any, password: str) -> bool:
    # Poll with a short per-selector timeout so Target's React UI has ~30s to render
    # the password step. The full 2500ms-per-selector timeout in _fill_first is too
    # slow to retry effectively within a tight deadline.
    selectors = [
        'input[data-test="password"]',
        'input[name="password"]',
        'input[id="password"]',
        'input[type="password"]',
        'input[autocomplete="current-password"]',
    ]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        html = _page_content(page)
        if _page_requires_human_intervention(html):
            _stop_on_intervention(html)
        for selector in selectors:
            try:
                page.locator(selector).first.fill(password, timeout=500, force=True)
                return True
            except Exception:
                continue
        page.wait_for_timeout(300)
    return False


def _fill_password(page: Any, password: str) -> bool:
    return _fill_first(
        page,
        [
            'input[data-test="password"]',
            'input[name="password"]',
            'input[id="password"]',
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ],
        [r"password"],
        password,
    )


def _fill_first(page: Any, selectors: list[str], label_patterns: list[str], value: str) -> bool:
    for selector in selectors:
        try:
            page.locator(selector).first.fill(value, timeout=2500)
            return True
        except Exception:
            continue
    for label in label_patterns:
        try:
            page.get_by_label(re.compile(label, re.IGNORECASE)).first.fill(value, timeout=2500)
            return True
        except Exception:
            continue
    return False


def _click_first_without_intervention(page: Any, labels: list[str], *, optional: bool) -> bool:
    deadline = time.monotonic() + (OPTIONAL_CLICK_DEADLINE_SECONDS if optional else DEFAULT_CLICK_DEADLINE_SECONDS)
    while time.monotonic() < deadline:
        for label in labels:
            pattern = re.compile(label, re.IGNORECASE)
            candidates = [
                page.get_by_role("button", name=pattern),
                page.get_by_role("link", name=pattern),
                page.get_by_text(pattern),
            ]
            for candidate in candidates:
                try:
                    candidate.first.click(timeout=CLICK_TIMEOUT_MS)
                    page.wait_for_load_state("domcontentloaded", timeout=CLICK_LOAD_STATE_TIMEOUT_MS)
                    page.wait_for_timeout(500)
                    return True
                except Exception:
                    continue
        page.wait_for_timeout(200)
    if optional:
        return False
    _write_debug_artifacts(page, "target_auto_login_click")
    return False


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
        *[("sign_in_required", pattern) for pattern in _SIGN_IN_PATTERNS],
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


_SIGN_IN_PATTERNS = [
    r"sign in to your target account",
    r"sign in to check out",
    r"enter your password",
    r"password is required",
    r"email or mobile phone",
]


def _page_requires_sign_in(html: str) -> bool:
    normalized = re.sub(r"\s+", " ", html.lower())
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in _SIGN_IN_PATTERNS)


def _page_requires_human_intervention(html: str) -> bool:
    normalized = re.sub(r"\s+", " ", html.lower())
    return bool(
        re.search(
            r"\bcaptcha\b|verify you(?:'| a)?re (?:a )?human|not a robot|verification code|two[- ]factor|multi[- ]factor|one[- ]time (?:passcode|password|code)",
            normalized,
            re.IGNORECASE,
        )
    )


def _page_has_remembered_target_account(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower())
    return "not you?" in normalized and "enter your password" in normalized


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


def _wait_for_checkout_ready(
    page: Any,
    profile: dict[str, Any],
    *,
    target_credentials: TargetCredentials | None = None,
) -> None:
    postal_code = ""
    shipping = profile.get("shipping_address") if isinstance(profile, dict) else None
    if isinstance(shipping, dict):
        postal_code = str(shipping.get("postal_code", "")).strip()

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        html = _page_content(page)
        if target_credentials is not None and _page_requires_sign_in(html):
            _ensure_target_signed_in(page, target_credentials)
            html = _page_content(page)
        _stop_on_intervention(html)
        text = _page_text(page)
        normalized = re.sub(r"\s+", " ", text.lower())
        on_cart = "cart" in page.url and "checkout" not in page.url
        if on_cart:
            page.wait_for_timeout(400)
            continue
        if postal_code and postal_code in text:
            return
        if re.search(r"place\s+(?:your\s+)?order|submit\s+order|order summary|payment", normalized):
            return
        page.wait_for_timeout(400)


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
