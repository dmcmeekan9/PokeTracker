from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import requests

from poketracker.checkout.dry_run import UnconfiguredCheckoutAdapter
from poketracker.checkout.http import HttpCheckoutAdapter
from poketracker.models import (
    Decision,
    DecisionType,
    ProductType,
    Retailer,
    SellerClassification,
    WatchlistItem,
)


class Response:
    def __init__(self, status_code: int, data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self) -> dict:
        if self._data is None:
            raise ValueError("not json")
        return self._data


def decision(quantity: int = 1) -> Decision:
    observed_price = Decimal("59.99")
    return Decision(
        type=DecisionType.WOULD_BUY,
        item=WatchlistItem(
            id="target-ascended-heroes-etb",
            name="Target: Ascended Heroes Elite Trainer Box",
            retailer=Retailer.TARGET,
            url="https://www.target.com/p/example/-/A-95082118",
            type=ProductType.ETB,
            msrp=Decimal("59.99"),
            max_quantity=quantity,
            enabled=True,
            sku="95082118",
        ),
        reason="would buy",
        observed_price=observed_price,
        msrp=Decimal("59.99"),
        seller=SellerClassification.RETAILER,
        quantity=quantity,
        weekly_spend_before=Decimal("0"),
        weekly_spend_after=observed_price * quantity,
        url="https://www.target.com/p/example/-/A-95082118",
    )


def test_http_checkout_posts_purchase_payload(monkeypatch) -> None:
    calls = []

    def post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return Response(200, {"status": "ordered", "order_id": "ABC123", "message": "confirmed"})

    monkeypatch.setattr("requests.post", post)

    result = HttpCheckoutAdapter("https://checkout.example.com/purchase", bearer_token="secret").execute(decision())

    assert result.type == DecisionType.PURCHASED
    assert result.checkout_status == "ordered"
    assert result.checkout_order_id == "ABC123"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["json"]["item"]["id"] == "target-ascended-heroes-etb"
    assert calls[0]["json"]["observed_price"] == "59.99"
    assert calls[0]["timeout"] == 285


def test_http_checkout_records_actual_quantity(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: Response(
            200,
            {
                "status": "ordered",
                "order_id": "ABC123",
                "message": "confirmed",
                "quantity": 1,
            },
        ),
    )

    result = HttpCheckoutAdapter("https://checkout.example.com/purchase").execute(decision(quantity=2))

    assert result.type == DecisionType.PURCHASED
    assert result.quantity == 1
    assert result.weekly_spend_after == Decimal("59.99")


def test_http_checkout_records_rejection(monkeypatch) -> None:
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: Response(409, text="sold out"))

    result = HttpCheckoutAdapter("https://checkout.example.com/purchase").execute(decision())

    assert result.type == DecisionType.PURCHASE_FAILED
    assert result.weekly_spend_after == result.weekly_spend_before
    assert result.checkout_status == "409"
    assert "HTTP 409" in result.reason
    assert result.checkout_message == "sold out"


def test_http_checkout_records_request_error(monkeypatch) -> None:
    def post(*args, **kwargs):
        raise requests.Timeout("too slow")

    monkeypatch.setattr("requests.post", post)

    result = HttpCheckoutAdapter("https://checkout.example.com/purchase").execute(decision())

    assert result.type == DecisionType.PURCHASE_FAILED
    assert result.weekly_spend_after == result.weekly_spend_before
    assert result.checkout_status == "request_error"
    assert "too slow" in (result.checkout_message or "")


def test_unconfigured_checkout_fails_only_would_buy_decisions() -> None:
    result = UnconfiguredCheckoutAdapter().execute(decision())

    assert result.type == DecisionType.PURCHASE_FAILED
    assert result.weekly_spend_after == result.weekly_spend_before
    assert result.checkout_status == "unconfigured"

    skip_decision = replace(decision(), type=DecisionType.SKIP)
    assert UnconfiguredCheckoutAdapter().execute(skip_decision).type == DecisionType.SKIP
