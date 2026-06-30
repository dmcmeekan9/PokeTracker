from __future__ import annotations

import base64
import os
import json
import re
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from poketracker.checkout.target_credentials import TargetCredentials
from poketracker.checkout.target_storage_state import decode_storage_state_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest

def kill_cdp_service_workers(cdp_url: str) -> None:
    """Close all service_worker CDP targets via WebSocket before Playwright connects.

    Playwright asserts in _CRBrowser._onAttachedToTarget when it encounters an
    attached service_worker whose browserContextId it doesn't recognise (i.e. a
    context created by a previous Playwright session). The HTTP /json/close
    endpoint only works for page targets; we must use the browser-level CDP
    WebSocket to send Target.closeTarget for service workers.
    """
    parsed = urlparse(cdp_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9222

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json", timeout=5) as resp:
            targets = json.loads(resp.read())
        sw_ids = [t["id"] for t in targets if t.get("type") == "service_worker"]
    except Exception:
        return

    if not sw_ids:
        return

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=5) as resp:
            version = json.loads(resp.read())
        ws_url = urlparse(version["webSocketDebuggerUrl"])
        ws_path = ws_url.path
    except Exception:
        return

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(512)
        if b" 101 " not in buf[:100]:
            return
        for i, target_id in enumerate(sw_ids):
            _ws_send(sock, json.dumps({"id": i + 1, "method": "Target.closeTarget", "params": {"targetId": target_id}}))
            try:
                sock.recv(256)
            except Exception:
                pass
        sock.close()
    except Exception:
        pass


def _ws_send(sock: socket.socket, message: str) -> None:
    data = message.encode()
    n = len(data)
    mask = os.urandom(4)
    header = bytearray([0x81])
    if n < 126:
        header.append(0x80 | n)
    else:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    header += mask
    sock.sendall(bytes(header) + bytes(d ^ mask[i % 4] for i, d in enumerate(data)))


TARGET_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CLICK_STEP_DEADLINES = {
    "add_to_cart": 12,
    "cart_or_checkout": 2,
    "checkout": 8,
    "place_order": 20,
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
                [r"check\s*out\s*(all)?", r"sign in to check out"],
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
                # Unregister any service workers so the checkout Lambda's connect_over_cdp
                # doesn't crash on pre-existing SW targets (Playwright CDP assertion in
                # _CRBrowser._onAttachedToTarget when a SW is already attached).
                try:
                    page.evaluate(
                        "async () => { const r = await navigator.serviceWorker.getRegistrations();"
                        " await Promise.all(r.map(x => x.unregister())); }"
                    )
                    page.wait_for_timeout(300)
                except Exception:
                    pass
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


def _add_to_cart_via_js(page: Any) -> bool:
    try:
        clicked = page.evaluate(
            """() => {
                const needles = ['add to cart', 'add for shipping', 'ship it'];
                const btns = Array.from(document.querySelectorAll('button'));
                for (const needle of needles) {
                    const btn = btns.find(
                        b => !b.disabled && b.innerText.trim().toLowerCase().includes(needle)
                    );
                    if (btn) {
                        btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                        return true;
                    }
                }
                return false;
            }"""
        )
        if clicked:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


def _add_to_cart(page: Any, url: str, target_credentials: TargetCredentials | None) -> None:
    for attempt in range(3):
        if attempt > 0:
            # Brief pause before reload to allow CDN cache to propagate fresh stock state
            page.wait_for_timeout(1500)
            _goto_target_page(page, url)
            _ensure_target_signed_in(page, target_credentials)
            _stop_on_intervention(_page_content(page))
        if _add_to_cart_via_js(page):
            return
        if _click_first(page, [r"add to cart", r"add for shipping", r"ship it"], "add_to_cart", optional=True):
            return
        if _page_indicates_cart_has_item(_page_content(page)):
            return
        try:
            body_text = page.locator("body").inner_text(timeout=2000)
            print(f"[add_to_cart] attempt={attempt} url={page.url!r} body={body_text[:800]!r}")
        except Exception as dbg_exc:
            print(f"[add_to_cart] attempt={attempt} debug_failed={dbg_exc}")
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
    try:
        _diag = f"url={getattr(page,'url','?')} text={re.sub(chr(32)+'+', ' ', _page_text(page))[:600]!r}"
    except Exception:
        _diag = "unavailable"
    raise CheckoutWebhookError(409, f"target_{step}_not_found", f"Target checkout could not find the {step} control. {_diag}")


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
    if step == "place_order":
        candidates.extend(
            [
                page.locator('button[data-test="placeOrderButton"]'),
                page.locator('button[data-test="place-order"]'),
                page.locator('[data-test*="placeOrder"] button'),
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
    # JavaScript-based dismissal is more reliable than Playwright's accessibility-tree
    # locators when modals trap focus or use non-standard aria attributes (e.g. the
    # "Health Data Consent" dialog that appears on first visit to a fresh Chrome profile).
    try:
        dismissed = page.evaluate(
            """() => {
                const labels = ['continue shopping', 'close'];
                const btns = Array.from(document.querySelectorAll('button'));
                for (const label of labels) {
                    const btn = btns.find(
                        b => b.innerText.trim().toLowerCase().includes(label)
                    );
                    if (btn) {
                        btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                        return true;
                    }
                }
                return false;
            }"""
        )
        if dismissed:
            page.wait_for_timeout(500)
            return
    except Exception:
        pass
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
        # Only click "continue" or "next" — NOT "sign in" or "log in".
        # Those fire after the password is filled; clicking them here
        # (before the password) would submit an empty password form.
        _click_first_without_intervention(
            page, [r"continue", r"next"], optional=True
        )
        # Target now defaults to magic-link/passkey after the username step.
        # Explicitly select password auth so the password field appears.
        _click_first_without_intervention(
            page, [r"enter.{0,10}password", r"use.{0,10}password", r"sign in with password"], optional=True
        )
    if not _fill_password_after_username(page, target_credentials.password):
        try:
            _url = getattr(page, "url", "unknown")
            _text = re.sub(r"\s+", " ", _page_text(page))
            # Pull out dialog/modal text if present
            _modal = re.search(r'role=["\']dialog["\']', _page_content(page))
            _diag = f"url={_url} modal={'yes' if _modal else 'no'} text={_text[800:1600]!r}"
        except Exception:
            _diag = "unavailable"
        raise CheckoutWebhookError(
            409, "sign_in_required",
            f"Target sign-in form did not expose the password field. {_diag}"
        )

    if not _click_first_without_intervention(page, [r"sign in", r"log in", r"continue"], optional=False):
        raise CheckoutWebhookError(409, "sign_in_required", "Target sign-in form did not expose the sign-in button")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        html = _page_content(page)
        # Two-pronged completion check:
        # 1. Visible body text has no sign-in patterns (handles modal close — hidden
        #    modal DOM nodes still contain "email or mobile phone" in raw HTML).
        # 2. Page URL is not a standalone /login page — full-page sign-in success
        #    navigates away; input placeholders don't appear in inner_text() so
        #    body_text alone can't detect a still-open login page.
        body_text = _page_text(page)
        page_url = getattr(page, "url", "")
        signed_in = not _page_requires_sign_in(body_text) and "/login" not in page_url
        if signed_in:
            return True
        if _page_requires_human_intervention(html):
            _stop_on_intervention(html)
        # "Verify it's you" is identity verification but isn't caught by
        # _page_requires_human_intervention — surface it explicitly.
        if re.search(r"verify it.?s you|we sent a code|check your email|check your phone", html, re.IGNORECASE):
            raise CheckoutWebhookError(409, "identity_verification", "Target requires identity verification after sign-in")

    _write_debug_artifacts(page, "target_auto_login")
    try:
        _diag = re.sub(r"\s+", " ", _page_content(page))[:600]
    except Exception:
        _diag = "unavailable"
    raise CheckoutWebhookError(409, "sign_in_required", f"Target auto-login did not complete. page_snippet={_diag!r}")


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


def _select_saved_payment(page: Any) -> bool:
    """Try to select the first saved/default payment method on Target's checkout page."""
    selectors = [
        '[data-test*="creditCard"] input[type="radio"]',
        '[data-test*="payment"] input[type="radio"]:not(:checked)',
        'input[type="radio"][name*="payment"]:not(:checked)',
        'input[type="radio"][id*="payment"]:not(:checked)',
        '[data-test*="savedCard"] input[type="radio"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            locator.check(timeout=2000, force=True)
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            page.wait_for_timeout(500)
            return True
        except Exception:
            continue
    # Fallback: click any visible button in the payment section
    try:
        page.get_by_role("button", name=re.compile(r"use.*card|select.*card|confirm.*payment", re.IGNORECASE)).first.click(timeout=2000)
        page.wait_for_timeout(500)
        return True
    except Exception:
        pass
    return False


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
        r"payment (?:could not|can't|cannot|was not) (?:be )?(?:authorized|processed|verified)",
        r"card (?:declined|was declined|could not be verified)",
    ]
    for pattern in hard_payment_interventions:
        if re.search(pattern, normalized, re.IGNORECASE):
            raise CheckoutWebhookError(409, "payment_intervention", "Target checkout requires intervention: payment_intervention")

    # CVV/CVC as a display label is normal on the final review page; only block if not yet at place-order
    soft_payment_interventions = [
        r"\bcvv\b",
        r"\bcvc\b",
        r"select (?:a )?payment method",
        r"select payment type",
        r"add (?:a )?payment method",
        r"update (?:your )?payment",
    ]
    if not ready_to_order:
        for pattern in soft_payment_interventions:
            if re.search(pattern, normalized, re.IGNORECASE):
                snippet = normalized[max(0, normalized.find("payment")-50):normalized.find("payment")+200]
                raise CheckoutWebhookError(
                    409,
                    "payment_intervention",
                    f"Target checkout requires intervention: payment_intervention. snippet={snippet!r}",
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

    deadline = time.monotonic() + 60
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
        # Ready when the place-order button is visible
        if re.search(r"place\s+(?:your\s+)?order|submit\s+order", normalized):
            return
        # Try to select payment if Target is prompting for it
        if re.search(r"select.*payment|add.*payment|choose.*payment|payment method", normalized):
            _select_saved_payment(page)
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
