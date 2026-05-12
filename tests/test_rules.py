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


def item(max_quantity: int = 1) -> WatchlistItem:
    return WatchlistItem(
        id="bestbuy-sample-etb",
        name="Sample ETB",
        retailer=Retailer.BESTBUY,
        url="https://www.bestbuy.com/site/sample/123.p",
        type=ProductType.ETB,
        msrp=Decimal("49.99"),
        max_quantity=max_quantity,
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
    assert decision.reason.startswith("dry-run would buy:")


def test_would_buy_reason_omits_dry_run_when_purchasing_enabled() -> None:
    signal = StockSignal(
        item=item(),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("49.99"),
        seller=SellerClassification.RETAILER,
    )
    real_run_engine = RulesEngine(
        GlobalConfig(
            purchasing_enabled=True,
            weekly_spend_cap=Decimal("150"),
            timezone="America/Chicago",
        )
    )

    decision = real_run_engine.evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "WOULD_BUY"
    assert decision.reason.startswith("would buy:")


def test_allows_price_below_msrp() -> None:
    signal = StockSignal(
        item=item(),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("44.99"),
        seller=SellerClassification.RETAILER,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "WOULD_BUY"


def test_quantity_two_counts_against_weekly_cap() -> None:
    signal = StockSignal(
        item=item(max_quantity=2),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("49.99"),
        seller=SellerClassification.RETAILER,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("0"))

    assert decision.type.value == "WOULD_BUY"
    assert decision.quantity == 2
    assert decision.weekly_spend_after == Decimal("99.98")


def test_uses_affordable_quantity_when_two_would_exceed_cap() -> None:
    signal = StockSignal(
        item=item(max_quantity=2),
        status=SignalStatus.IN_STOCK,
        observed_price=Decimal("49.99"),
        seller=SellerClassification.RETAILER,
    )

    decision = engine().evaluate(signal, weekly_spend_before=Decimal("100.01"))

    assert decision.type.value == "WOULD_BUY"
    assert decision.quantity == 1
    assert decision.weekly_spend_after == Decimal("150.00")


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
