from __future__ import annotations

from decimal import Decimal

import requests

from poketracker.models import ProductType, Retailer, SellerClassification, SignalStatus, WatchlistItem
from poketracker.signals.page import RetailerPageSignalAdapter, _classify_seller, _extract_price, _extract_status


def test_disabled_add_to_cart_button_is_out_of_stock() -> None:
    html = '<button type="button" disabled="">Add to cart</button>'

    assert _extract_status(html) == SignalStatus.OUT_OF_STOCK


def test_enabled_add_to_cart_button_is_in_stock() -> None:
    html = '<button type="button">Add to cart</button>'

    assert _extract_status(html) == SignalStatus.IN_STOCK


def test_ignores_generic_free_shipping_price() -> None:
    html = '<meta content="Free standard shipping with $35 orders.">'

    assert _extract_price(html) is None


def test_extracts_structured_price() -> None:
    html = '{"current_retail":59.99}'

    assert _extract_price(html) == Decimal("59.99")


def test_target_without_marketplace_marker_is_retailer() -> None:
    seller, seller_name = _classify_seller(item(), "target product detail page")

    assert seller == SellerClassification.RETAILER
    assert seller_name == "Target"


def test_target_in_stock_uses_msrp_when_price_is_deferred(monkeypatch) -> None:
    class Response:
        status_code = 200
        text = '<button type="button">Add to cart</button>'

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())

    signal = RetailerPageSignalAdapter().check(item())

    assert signal.status == SignalStatus.IN_STOCK
    assert signal.observed_price == Decimal("59.99")
    assert signal.seller == SellerClassification.RETAILER


def test_target_in_stock_signal_becomes_would_buy(monkeypatch) -> None:
    from poketracker.models import GlobalConfig
    from poketracker.rules.engine import RulesEngine

    class Response:
        status_code = 200
        text = '<button type="button">Add to cart</button>'

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())

    signal = RetailerPageSignalAdapter().check(item())
    decision = RulesEngine(
        GlobalConfig(
            purchasing_enabled=False,
            weekly_spend_cap=Decimal("150"),
            timezone="America/Chicago",
        )
    ).evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "WOULD_BUY"
    assert decision.observed_price == Decimal("59.99")


def test_target_timeout_retries_and_becomes_unknown(monkeypatch) -> None:
    attempts = 0

    def raise_timeout(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise requests.ConnectTimeout("connect timeout")

    monkeypatch.setattr("requests.get", raise_timeout)

    signal = RetailerPageSignalAdapter(max_attempts=2, retry_delay_seconds=0).check(item())

    assert attempts == 2
    assert signal.status == SignalStatus.UNKNOWN
    assert "transient network failure" in (signal.message or "")


def item() -> WatchlistItem:
    return WatchlistItem(
        id="target-ascended-heroes-etb",
        name="Target: Ascended Heroes Elite Trainer Box",
        retailer=Retailer.TARGET,
        url="https://www.target.com/p/example/-/A-95082118",
        type=ProductType.ETB,
        msrp=Decimal("59.99"),
        max_quantity=1,
        enabled=True,
        sku="95082118",
    )
