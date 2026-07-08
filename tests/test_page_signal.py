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


def test_enabled_add_to_cart_button_wins_over_generic_out_of_stock_text() -> None:
    html = '<script>{"availability_status":"OUT_OF_STOCK"}</script><button type="button">Add to cart</button>'

    assert _extract_status(html) == SignalStatus.IN_STOCK


def test_enabled_ship_it_button_is_in_stock() -> None:
    html = '<button type="button">Ship it</button>'

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
    assert "html=in_stock" in (signal.message or "")
    assert "redsky=unknown" in (signal.message or "")


def test_target_redsky_fulfillment_overrides_disabled_server_button(monkeypatch) -> None:
    html = (
        '<script>window.__CONFIG__ = JSON.parse("{\\"services\\":{\\"redsky\\":'
        '{\\"baseUrl\\":\\"https://redsky.target.com\\",\\"apiKey\\":\\"test-key\\"}}}")</script>'
        '<script id="__NEXT_DATA__" type="application/json">{"visitor_id":"visitor-1"}</script>'
        '<button type="button" disabled="">Add to cart</button>'
    )
    calls = []

    class PageResponse:
        status_code = 200
        text = html

    class FulfillmentResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "data": {
                    "product": {
                        "fulfillment": {
                            "sold_out": False,
                            "shipping_options": {
                                "availability_status": "IN_STOCK",
                                "available_to_promise_quantity": 10,
                            },
                        }
                    }
                }
            }

    def get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if "redsky_aggregations" in url:
            return FulfillmentResponse()
        return PageResponse()

    monkeypatch.setattr("requests.get", get)

    signal = RetailerPageSignalAdapter().check(item(sku="95045259"))

    assert signal.status == SignalStatus.IN_STOCK
    assert signal.observed_price == Decimal("59.99")
    assert calls[1]["headers"]["x-api-key"] == "test-key"
    assert calls[1]["params"]["tcin"] == "95045259"


def test_target_html_in_stock_wins_over_lagging_redsky_out_of_stock(monkeypatch) -> None:
    # HTML button is enabled (server says in stock) but Redsky API lags and returns OUT_OF_STOCK.
    # We trust the HTML — Redsky should never downgrade a confirmed HTML in-stock signal.
    html = (
        '<script>window.__CONFIG__ = JSON.parse("{\\"services\\":{\\"redsky\\":'
        '{\\"baseUrl\\":\\"https://redsky.target.com\\",\\"apiKey\\":\\"test-key\\"}}}")</script>'
        '<button type="button">Add to cart</button>'
    )

    class PageResponse:
        status_code = 200
        text = html

    class FulfillmentResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "data": {
                    "product": {
                        "fulfillment": {
                            "sold_out": False,
                            "shipping_options": {"availability_status": "OUT_OF_STOCK"},
                        }
                    }
                }
            }

    monkeypatch.setattr(
        "requests.get",
        lambda url, **kwargs: FulfillmentResponse() if "redsky_aggregations" in url else PageResponse(),
    )

    signal = RetailerPageSignalAdapter().check(item(sku="95045259"))

    assert signal.status == SignalStatus.IN_STOCK
    assert signal.observed_price == Decimal("59.99")


def test_target_redsky_out_of_stock_keeps_item_out_of_stock(monkeypatch) -> None:
    html = (
        '<script>window.__CONFIG__ = JSON.parse("{\\"services\\":{\\"redsky\\":'
        '{\\"baseUrl\\":\\"https://redsky.target.com\\",\\"apiKey\\":\\"test-key\\"}}}")</script>'
        '<button type="button" disabled="">Add to cart</button>'
    )

    class PageResponse:
        status_code = 200
        text = html

    class FulfillmentResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "data": {
                    "product": {
                        "fulfillment": {
                            "sold_out": True,
                            "shipping_options": {"availability_status": "OUT_OF_STOCK"},
                        }
                    }
                }
            }

    monkeypatch.setattr(
        "requests.get",
        lambda url, **kwargs: FulfillmentResponse() if "redsky_aggregations" in url else PageResponse(),
    )

    signal = RetailerPageSignalAdapter().check(item(sku="95045259"))

    assert signal.status == SignalStatus.OUT_OF_STOCK
    assert signal.observed_price is None


def test_target_redsky_checks_multiple_locations(monkeypatch) -> None:
    html = (
        '<script>window.__CONFIG__ = JSON.parse("{\\"services\\":{\\"redsky\\":'
        '{\\"baseUrl\\":\\"https://redsky.target.com\\",\\"apiKey\\":\\"test-key\\"}}}")</script>'
        '<button type="button" disabled="">Add to cart</button>'
    )
    monkeypatch.setenv("TARGET_FULFILLMENT_LOCATIONS", "50023,IA,41.73,-93.58;10001,NY,40.75,-73.99")
    redsky_calls = []

    class PageResponse:
        status_code = 200
        text = html

    class FulfillmentResponse:
        status_code = 200

        def __init__(self, availability_status: str) -> None:
            self.availability_status = availability_status

        def json(self) -> dict:
            return {
                "data": {
                    "product": {
                        "fulfillment": {
                            "sold_out": self.availability_status == "OUT_OF_STOCK",
                            "shipping_options": {"availability_status": self.availability_status},
                        }
                    }
                }
            }

    def get(url, **kwargs):
        if "redsky_aggregations" not in url:
            return PageResponse()
        redsky_calls.append(kwargs["params"]["zip"])
        status = "OUT_OF_STOCK" if len(redsky_calls) == 1 else "IN_STOCK"
        return FulfillmentResponse(status)

    monkeypatch.setattr("requests.get", get)

    signal = RetailerPageSignalAdapter().check(item(sku="95045259"))

    assert signal.status == SignalStatus.IN_STOCK
    assert signal.observed_price == Decimal("59.99")
    assert redsky_calls == ["50023", "10001"]
    assert "50023/IA:out_of_stock;10001/NY:in_stock" in (signal.message or "")


def test_target_redsky_unknown_message_includes_failure_details(monkeypatch) -> None:
    html = (
        '<script>window.__CONFIG__ = JSON.parse("{\\"services\\":{\\"redsky\\":'
        '{\\"baseUrl\\":\\"https://redsky.target.com\\",\\"apiKey\\":\\"test-key\\"}}}")</script>'
        '<button type="button" disabled="">Add to cart</button>'
    )
    monkeypatch.setenv("TARGET_FULFILLMENT_LOCATIONS", "50023,IA,41.73,-93.58")

    class PageResponse:
        status_code = 200
        text = html

    class FulfillmentResponse:
        status_code = 503

    monkeypatch.setattr(
        "requests.get",
        lambda url, **kwargs: FulfillmentResponse() if "redsky_aggregations" in url else PageResponse(),
    )

    signal = RetailerPageSignalAdapter().check(item(sku="95045259"))

    assert signal.status == SignalStatus.OUT_OF_STOCK
    assert "redsky=unknown (50023/IA:http_503)" in (signal.message or "")


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


def item(sku: str = "95082118") -> WatchlistItem:
    return WatchlistItem(
        id="target-ascended-heroes-etb",
        name="Target: Ascended Heroes Elite Trainer Box",
        retailer=Retailer.TARGET,
        url="https://www.target.com/p/example/-/A-95082118",
        type=ProductType.ETB,
        msrp=Decimal("59.99"),
        max_quantity=1,
        enabled=True,
        sku=sku,
    )
