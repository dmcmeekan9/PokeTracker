from __future__ import annotations

import sys
import types
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from poketracker.checkout import local_target_buyer
from poketracker.checkout_webhook.handler_types import PurchaseRequest
from poketracker.models import Retailer


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None) -> "FixedDatetime":
        return cls(2026, 5, 12, 23, 50, tzinfo=tz)


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.target.com/p/example"

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        _ = wait_until
        _ = timeout
        self.url = url

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        _ = state
        _ = timeout

    def wait_for_timeout(self, timeout: int) -> None:
        _ = timeout


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]

    def new_page(self) -> FakePage:
        return self.pages[0]


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.contexts = [context]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakePlaywrightManager:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = types.SimpleNamespace(connect_over_cdp=lambda cdp_url: browser)

    def __enter__(self) -> "FakePlaywrightManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = exc_type
        _ = exc
        _ = tb


def test_parse_wait_until_rolls_hhmm_into_next_day(monkeypatch) -> None:
    monkeypatch.setattr(local_target_buyer, "datetime", FixedDatetime)

    parsed = local_target_buyer._parse_wait_until("01:55", "America/Chicago")

    assert parsed.isoformat() == "2026-05-13T01:55:00-05:00"


def test_default_verify_url_uses_first_enabled_target_item() -> None:
    config = SimpleNamespace(
        items=[
            SimpleNamespace(enabled=True, retailer=Retailer.WALMART, url="https://example.com/walmart"),
            SimpleNamespace(enabled=False, retailer=Retailer.TARGET, url="https://example.com/disabled"),
            SimpleNamespace(enabled=True, retailer=Retailer.TARGET, url="https://example.com/target"),
        ]
    )

    assert local_target_buyer._default_verify_url(config) == "https://example.com/target"


def test_purchase_target_item_from_cdp_uses_attached_browser(monkeypatch) -> None:
    page = FakePage()
    browser = FakeBrowser(FakeContext(page))
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.TimeoutError = RuntimeError
    fake_sync_api.sync_playwright = lambda: FakePlaywrightManager(browser)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(local_target_buyer, "_page_content", lambda page: "<main>ready</main>")
    monkeypatch.setattr(local_target_buyer, "_stop_on_intervention", lambda html: None)
    monkeypatch.setattr(local_target_buyer, "_click_first", lambda page, labels, step, optional=False: True)
    monkeypatch.setattr(local_target_buyer, "_page_indicates_cart_has_item", lambda html: False)
    monkeypatch.setattr(local_target_buyer, "_select_standard_shipping", lambda page: None)
    monkeypatch.setattr(local_target_buyer, "_set_target_quantity", lambda page, quantity: quantity)
    monkeypatch.setattr(local_target_buyer, "_wait_for_checkout_ready", lambda page, profile, **kwargs: None)
    monkeypatch.setattr(local_target_buyer, "_verify_checkout_profile_visible", lambda html, profile, *args: None)
    monkeypatch.setattr(local_target_buyer, "_extract_order_id", lambda html: "ABC123")

    request = PurchaseRequest(
        item_id="target-item",
        item_name="Target Item",
        retailer="target",
        sku="95045259",
        url="https://www.target.com/p/example",
        quantity=2,
        observed_price=Decimal("14.99"),
        msrp=Decimal("14.99"),
    )
    result = local_target_buyer.purchase_target_item_from_cdp(
        "http://127.0.0.1:9222",
        request,
        {"shipping_address": {"postal_code": "50023"}},
        place_order_enabled=True,
    )

    assert result.status == "ordered"
    assert result.order_id == "ABC123"
    assert result.quantity == 2
    assert browser.closed is True


def test_purchase_target_item_from_cdp_verify_only_stops_before_order(monkeypatch) -> None:
    page = FakePage()
    browser = FakeBrowser(FakeContext(page))
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.TimeoutError = RuntimeError
    fake_sync_api.sync_playwright = lambda: FakePlaywrightManager(browser)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(local_target_buyer, "_page_content", lambda page: "<main>ready</main>")
    monkeypatch.setattr(local_target_buyer, "_stop_on_intervention", lambda html: None)
    monkeypatch.setattr(local_target_buyer, "_click_first", lambda page, labels, step, optional=False: True)
    monkeypatch.setattr(local_target_buyer, "_page_indicates_cart_has_item", lambda html: False)
    monkeypatch.setattr(local_target_buyer, "_select_standard_shipping", lambda page: None)
    monkeypatch.setattr(local_target_buyer, "_set_target_quantity", lambda page, quantity: quantity)
    monkeypatch.setattr(local_target_buyer, "_wait_for_checkout_ready", lambda page, profile, **kwargs: None)
    monkeypatch.setattr(local_target_buyer, "_verify_checkout_profile_visible", lambda html, profile, *args: None)
    verified = {}
    monkeypatch.setattr(
        local_target_buyer,
        "_verify_click_candidate_present",
        lambda page, labels, step: verified.update({"step": step}),
    )

    request = PurchaseRequest(
        item_id="target-item",
        item_name="Target Item",
        retailer="target",
        sku="95045259",
        url="https://www.target.com/p/example",
        quantity=1,
        observed_price=Decimal("14.99"),
        msrp=Decimal("14.99"),
    )
    result = local_target_buyer.purchase_target_item_from_cdp(
        "http://127.0.0.1:9222",
        request,
        {"shipping_address": {"postal_code": "50023"}},
        place_order_enabled=False,
        verify_only=True,
    )

    assert result.status == "ready_to_place_order"
    assert result.order_id is None
    assert verified == {"step": "place_order"}
    assert browser.closed is True
