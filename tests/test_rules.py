from __future__ import annotations

from decimal import Decimal

from poketracker.models import (
    GlobalConfig,
    ProductType,
    Retailer,
    SellerClassification,
    SignalStatus,
    StockSignal,
    WatchlistItem,
)
from poketracker.rules.engine import RulesEngine


def item() -> WatchlistItem:
    return WatchlistItem(
        id="bestbuy-sample-etb",
        name="Sample ETB",
        retailer=Retailer.BESTBUY,
        url="https://www.bestbuy.com/site/sample/123.p",
        type=ProductType.ETB,
        msrp=Decimal("49.99"),
        max_quantity=1,
        enabled=True,
        sku="123",
    )


def engine() -> RulesEngine:
    return RulesEngine(
        GlobalConfig(
            purchasing_enabled=False,
            weekly_spend_cap=Decimal("150"),
            timezone="America/Chicago",
        )
    )


def test_would_buy_when_retailer_and_price_at_msrp() -> None:
    signal = StockSignal(
        item=item(),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("49.99"),
        seller=SellerClassification.RETAILER,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "WOULD_BUY"
    assert decision.weekly_spend_after == Decimal("49.99")


def test_allows_price_below_msrp() -> None:
    signal = StockSignal(
        item=item(),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("44.99"),
        seller=SellerClassification.RETAILER,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "WOULD_BUY"


def test_third_party_is_fyi_only() -> None:
    signal = StockSignal(
        item=item(),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("44.99"),
        seller=SellerClassification.THIRD_PARTY,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "FYI_ONLY"


def test_skips_when_weekly_cap_exceeded() -> None:
    signal = StockSignal(
        item=item(),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("49.99"),
        seller=SellerClassification.RETAILER,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("125"))

    assert decision.type.value == "SKIP"
    assert "weekly spend cap" in decision.reason
