from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from poketracker.checkout.profile import load_checkout_profile
from poketracker.checkout.target_credentials import TargetCredentials
from poketracker.checkout.target_session import capture_target_session_from_cdp, upload_storage_state_secret
from poketracker.checkout.target_storage_state import decode_storage_state_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest
from poketracker.checkout_webhook.target_driver import (
    TargetCheckoutResult,
    _add_to_cart,
    _click_first,
    _click_first_with_auto_login,
    _extract_order_id,
    _ensure_target_signed_in,
    _goto_target_page,
    _new_target_context,
    _page_content,
    _page_indicates_cart_has_item,
    _resume_checkout_after_sign_in,
    _select_saved_payment,
    _set_target_quantity,
    _select_standard_shipping,
    _stop_on_intervention,
    _verify_click_candidate_present,
    _verify_checkout_profile_visible,
    _wait_for_checkout_ready,
    kill_cdp_service_workers,
    probe_cdp_endpoint,
    restart_cdp_browser_if_configured,
    resolve_cdp_browser_url,
)
from poketracker.config.watchlist import load_watchlist_file
from poketracker.models import DecisionType, Retailer, SellerClassification
from poketracker.rules.engine import RulesEngine
from poketracker.signals.page import RetailerPageSignalAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local Target checkout burst against an already-open debug Chrome.")
    parser.add_argument("--watchlist", default="watchlist.yaml", help="Watchlist YAML path.")
    parser.add_argument("--profile", default="checkout-profile.json", help="Local checkout profile JSON path.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="Chrome remote debugging URL.")
    parser.add_argument("--duration-seconds", type=int, default=600, help="How long to monitor.")
    parser.add_argument("--interval-seconds", type=int, default=10, help="Delay between checks.")
    parser.add_argument(
        "--wait-until",
        help="Optional local start time. Accepts HH:MM or an ISO timestamp such as 2026-05-13T01:55:00-05:00.",
    )
    parser.add_argument(
        "--refresh-session-first",
        action="store_true",
        help="Refresh the attached browser session before monitoring and optionally upload it to AWS.",
    )
    parser.add_argument("--session-output", default="target-session.json", help="Where to save the refreshed session JSON.")
    parser.add_argument(
        "--target-session-secret-id",
        default=os.environ.get("TARGET_SESSION_SECRET_ARN") or os.environ.get("TARGET_SESSION_SECRET_ID"),
        help="Optional Secrets Manager secret id or ARN to update after refreshing the browser session.",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="AWS region for session upload.")
    parser.add_argument(
        "--verify-url",
        help="Optional Target product URL to open during session refresh. Defaults to the first enabled Target item.",
    )
    parser.add_argument("--place-order", action="store_true", help="Allow clicking Target's final place-order button.")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Stop after confirming the final Target place-order control is visible.",
    )
    parser.add_argument("--once", action="store_true", help="Run one pass and exit.")
    args = parser.parse_args()

    profile = load_checkout_profile(args.profile)
    config = load_watchlist_file(args.watchlist)
    timezone_name = config.global_config.timezone
    _wait_until_start(args.wait_until, timezone_name)
    if args.refresh_session_first:
        verify_url = args.verify_url or _default_verify_url(config)
        storage_state = capture_target_session_from_cdp(args.session_output, args.cdp_url, verify_url=verify_url)
        if args.target_session_secret_id:
            encoding = upload_storage_state_secret(storage_state, args.target_session_secret_id, args.region)
            print(
                f"target session uploaded to {args.target_session_secret_id} ({encoding})",
                flush=True,
            )

    engine = RulesEngine(replace(config.global_config, purchasing_enabled=True))

    if not args.once:
        _run_prewarmed_burst(args, config, profile, engine)
        return

    # --once mode: single pass without pre-warming
    adapter = RetailerPageSignalAdapter(timeout_seconds=8, max_attempts=1)
    for item in config.items:
        if not item.enabled or item.retailer != Retailer.TARGET:
            continue

        signal = adapter.check(item)
        decision = engine.evaluate(signal, Decimal("0"))
        print(f"{item.id}: {signal.status.value} {decision.type.value} - {decision.reason}", flush=True)
        if decision.type != DecisionType.WOULD_BUY:
            continue

        request = PurchaseRequest(
            item_id=item.id,
            item_name=item.name,
            retailer=item.retailer.value,
            sku=item.sku,
            url=item.url,
            quantity=decision.quantity,
            observed_price=decision.observed_price or item.msrp,
            msrp=item.msrp,
        )
        try:
            result = purchase_target_item_from_cdp(
                args.cdp_url,
                request,
                profile,
                place_order_enabled=args.place_order,
                verify_only=args.verify_only or not args.place_order,
            )
        except CheckoutWebhookError as exc:
            print(f"{item.id}: checkout failed {exc.status} - {exc.message}", flush=True)
            continue

        print(f"{item.id}: checkout {result.status} quantity={result.quantity} order_id={result.order_id}", flush=True)


def purchase_target_item_from_cdp(
    cdp_url: str,
    request: PurchaseRequest,
    profile: dict[str, Any],
    *,
    place_order_enabled: bool,
    target_credentials: TargetCredentials | None = None,
    verify_only: bool = False,
    target_session_json: str | None = None,
) -> TargetCheckoutResult:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CheckoutWebhookError(503, "driver_dependency_missing", "Playwright is not installed") from exc

    cdp_probe = probe_cdp_endpoint(cdp_url)
    if cdp_probe.get("tcp") != "ok" or cdp_probe.get("http") != "ok":
        restart_cdp_browser_if_configured()
    kill_cdp_service_workers(cdp_url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(resolve_cdp_browser_url(cdp_url))
        try:
            prewarmed = _find_prewarmed_tab(browser, request.url)
            if prewarmed:
                context, page = prewarmed
                own_context = True
                # Reload for fresh stock state; warmer may have run up to 5 min ago.
                try:
                    page.goto(request.url, wait_until="commit", timeout=15000)
                    page.wait_for_timeout(300)
                except Exception:
                    pass
            else:
                # No pre-warmed tab — use Chrome's existing authenticated context
                # rather than creating a new one with storage_state. Creating a new
                # Playwright BrowserContext via CDP can fail on some Chrome/Playwright
                # version combinations; the existing context is already signed in via
                # the session refresh Lambda.
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                own_context = False
                page = context.new_page()
                _goto_target_page(page, request.url)
            try:
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
                    _verify_click_candidate_present(
                        page,
                        [r"place your order", r"place order", r"submit order"],
                        "place_order",
                    )
                    return TargetCheckoutResult(
                        status="ready_to_place_order",
                        order_id=None,
                        message="local Target checkout reached the final place-order control; no order was placed",
                        quantity=actual_quantity,
                        storage_state=_context_storage_state(context),
                    )

                _verify_checkout_profile_visible(_page_content(page), profile, page)

                if not place_order_enabled:
                    raise CheckoutWebhookError(
                        409,
                        "place_order_disabled",
                        "local Target checkout was prepared but --place-order was not passed",
                    )

                _click_first(page, [r"place your order", r"place order", r"submit order"], "place_order")
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                confirmation_html = _page_content(page)
                _stop_on_intervention(confirmation_html)
                return TargetCheckoutResult(
                    status="ordered",
                    order_id=_extract_order_id(confirmation_html),
                    message="Target checkout completed from local Chrome",
                    quantity=actual_quantity,
                    storage_state=_context_storage_state(context),
                )
            except PlaywrightTimeoutError as exc:
                raise CheckoutWebhookError(504, "target_timeout", f"Target checkout timed out: {exc}") from exc
            finally:
                if own_context:
                    context.close()
        finally:
            # For CDP this disconnects Playwright from Chrome; the user's browser remains open.
            browser.close()


def _context_storage_state(context: Any) -> dict[str, Any] | None:
    try:
        return context.storage_state()
    except Exception:
        return None


def _page_has_shipping_option(html: str) -> bool:
    """Fast pre-filter: true if the page HTML contains a shipping/ATC button indicator."""
    return bool(re.search(r'shippingButton', html))


def _checkout_from_prewarmed_tab(
    page: Any,
    request: PurchaseRequest,
    profile: dict[str, Any],
    *,
    place_order_enabled: bool,
    target_credentials: TargetCredentials | None = None,
    verify_only: bool = False,
) -> TargetCheckoutResult:
    """Run checkout on a tab already navigated to the product page (skip initial navigation)."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise CheckoutWebhookError(503, "driver_dependency_missing", "Playwright is not installed") from exc

    try:
        _ensure_target_signed_in(page, target_credentials)
        _stop_on_intervention(_page_content(page))
        _add_to_cart(page, request.url, target_credentials)
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
                message="local Target checkout reached the final place-order control; no order was placed",
                quantity=actual_quantity,
                storage_state=None,
            )

        _verify_checkout_profile_visible(_page_content(page), profile, page)

        if not place_order_enabled:
            raise CheckoutWebhookError(
                409,
                "place_order_disabled",
                "local Target checkout was prepared but --place-order was not passed",
            )

        _click_first(page, [r"place your order", r"place order", r"submit order"], "place_order")
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        confirmation_html = _page_content(page)
        _stop_on_intervention(confirmation_html)
        return TargetCheckoutResult(
            status="ordered",
            order_id=_extract_order_id(confirmation_html),
            message="Target checkout completed from pre-warmed local Chrome",
            quantity=actual_quantity,
            storage_state=None,
        )
    except PlaywrightTimeoutError as exc:
        raise CheckoutWebhookError(504, "target_timeout", f"Target checkout timed out: {exc}") from exc


def _run_prewarmed_burst(
    args: argparse.Namespace,
    config: Any,
    profile: dict[str, Any],
    engine: Any,
) -> None:
    """Burst loop that pre-navigates EC2 browser tabs so checkout skips the initial page load."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CheckoutWebhookError(503, "driver_dependency_missing", "Playwright is not installed") from exc

    enabled_items = [item for item in config.items if item.enabled and item.retailer == Retailer.TARGET]
    purchased_item_ids: set[str] = set()
    local_spend = Decimal("0")
    deadline = time.monotonic() + args.duration_seconds
    signal_adapter = RetailerPageSignalAdapter(timeout_seconds=8, max_attempts=1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()

            item_pages: dict[str, Any] = {}
            for item in enabled_items:
                page = context.new_page()
                try:
                    _goto_target_page(page, item.url)
                    _ensure_target_signed_in(page, None)
                    item_pages[item.id] = page
                    print(f"pre-warmed tab: {item.id}", flush=True)
                except Exception as exc:
                    print(f"warn: could not pre-warm {item.id}: {exc}", flush=True)

            iteration = 1
            while True:
                print(f"--- burst iteration {iteration} ---", flush=True)
                for item in enabled_items:
                    if item.id in purchased_item_ids or item.id not in item_pages:
                        continue

                    page = item_pages[item.id]
                    try:
                        page.goto(item.url, wait_until="commit", timeout=15000)
                        page.wait_for_timeout(300)
                    except Exception as exc:
                        print(f"{item.id}: reload failed: {exc}", flush=True)
                        continue

                    # Skip HTTP signal check when page shows no shipping option
                    if not _page_has_shipping_option(_page_content(page)):
                        print(f"{item.id}: no shipping option visible", flush=True)
                        continue

                    signal = signal_adapter.check(item)
                    decision = engine.evaluate(signal, local_spend)
                    print(f"{item.id}: {signal.status.value} {decision.type.value} - {decision.reason}", flush=True)
                    if decision.type != DecisionType.WOULD_BUY:
                        continue

                    request = PurchaseRequest(
                        item_id=item.id,
                        item_name=item.name,
                        retailer=item.retailer.value,
                        sku=item.sku,
                        url=item.url,
                        quantity=decision.quantity,
                        observed_price=decision.observed_price or item.msrp,
                        msrp=item.msrp,
                    )
                    try:
                        result = _checkout_from_prewarmed_tab(
                            page,
                            request,
                            profile,
                            place_order_enabled=args.place_order,
                            verify_only=args.verify_only or not args.place_order,
                        )
                    except CheckoutWebhookError as exc:
                        print(f"{item.id}: checkout failed {exc.status} - {exc.message}", flush=True)
                    else:
                        print(
                            f"{item.id}: checkout {result.status} quantity={result.quantity} order_id={result.order_id}",
                            flush=True,
                        )
                        if result.status == "ordered":
                            purchased_item_ids.add(item.id)
                            local_spend += request.observed_price * result.quantity

                    try:
                        _goto_target_page(page, item.url)
                    except Exception:
                        pass

                if time.monotonic() >= deadline:
                    break
                iteration += 1
                time.sleep(args.interval_seconds)
        finally:
            browser.close()


def _find_prewarmed_tab(browser: Any, url: str) -> tuple[Any, Any] | None:
    """Return (context, page) if the tab warmer left a tab already loaded at this URL."""
    normalized = url.rstrip("/")
    try:
        for context in browser.contexts:
            for page in context.pages:
                page_url = getattr(page, "url", None)
                if page_url and page_url.rstrip("/") == normalized:
                    return context, page
    except Exception:
        pass
    return None


def _default_verify_url(config: Any) -> str | None:
    for item in config.items:
        if item.enabled and item.retailer == Retailer.TARGET:
            return item.url
    return None


def _wait_until_start(wait_until: str | None, timezone_name: str) -> None:
    if not wait_until:
        return
    start_at = _parse_wait_until(wait_until, timezone_name)
    remaining = (start_at - datetime.now(start_at.tzinfo)).total_seconds()
    if remaining <= 0:
        return
    print(f"waiting until {start_at.isoformat()} before starting local Target burst", flush=True)
    time.sleep(remaining)


def _parse_wait_until(raw: str, timezone_name: str) -> datetime:
    timezone = _resolve_timezone(timezone_name)
    if len(raw) == 5 and raw[2] == ":":
        hour, minute = raw.split(":", maxsplit=1)
        now = datetime.now(timezone)
        candidate = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _resolve_timezone(timezone_name: str) -> Any:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_timezone = datetime.now().astimezone().tzinfo
        if local_timezone is None:
            raise
        print(
            f"warning: timezone data for {timezone_name!r} is unavailable; falling back to the local system timezone",
            file=sys.stderr,
            flush=True,
        )
        return local_timezone


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
