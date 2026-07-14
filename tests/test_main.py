from __future__ import annotations

from decimal import Decimal

from poketracker import main as poketracker_main
from poketracker.models import (
    Decision,
    DecisionType,
    ProductType,
    Retailer,
    SellerClassification,
    SignalStatus,
    StockSignal,
    WatchlistItem,
)


def test_optional_positive_int_accepts_positive_value(monkeypatch) -> None:
    monkeypatch.setenv("POKETRACKER_BURST_DURATION_SECONDS", "600")

    assert poketracker_main._optional_positive_int("POKETRACKER_BURST_DURATION_SECONDS") == 600


def test_optional_positive_int_ignores_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("POKETRACKER_BURST_DURATION_SECONDS", "nope")

    assert poketracker_main._optional_positive_int("POKETRACKER_BURST_DURATION_SECONDS") is None


def test_burst_runs_until_duration_expires(monkeypatch) -> None:
    runs = []
    now = {"value": 0.0}

    def run_once() -> None:
        runs.append(now["value"])
        now["value"] += 1.0

    def monotonic() -> float:
        return now["value"]

    def sleep(seconds: float) -> None:
        now["value"] += seconds

    monkeypatch.setattr(poketracker_main, "run_once", run_once)
    monkeypatch.setattr(poketracker_main.time, "monotonic", monotonic)
    monkeypatch.setattr(poketracker_main.time, "sleep", sleep)

    poketracker_main._run_burst(duration_seconds=25, interval_seconds=10)

    assert runs == [0.0, 11.0, 22.0]


def test_target_stock_probe_requires_configured_item_and_redsky_403(monkeypatch) -> None:
    poketracker_main._LAST_TARGET_PROBE_AT.clear()
    monkeypatch.setenv("TARGET_STOCK_PROBE_ITEM_IDS", "target-ascended-heroes-booster-bundle")
    monkeypatch.setattr(poketracker_main.time, "monotonic", lambda: 100.0)

    assert poketracker_main._should_probe_target_stock(
        target_signal(
            item_id="target-ascended-heroes-booster-bundle",
            message="html=out_of_stock redsky=unknown (50023/IA:http_403)",
        )
    )
    assert not poketracker_main._should_probe_target_stock(
        target_signal(
            item_id="target-ascended-heroes-etb",
            message="html=out_of_stock redsky=unknown (50023/IA:http_403)",
        )
    )
    assert not poketracker_main._should_probe_target_stock(
        target_signal(
            item_id="target-ascended-heroes-booster-bundle",
            message="html=out_of_stock redsky=unknown (50023/IA:http_503)",
        )
    )


def test_target_stock_probe_obeys_cooldown(monkeypatch) -> None:
    poketracker_main._LAST_TARGET_PROBE_AT.clear()
    monkeypatch.setenv("TARGET_STOCK_PROBE_ITEM_IDS", "target-ascended-heroes-booster-bundle")
    monkeypatch.setenv("TARGET_STOCK_PROBE_COOLDOWN_SECONDS", "30")
    now = {"value": 100.0}
    monkeypatch.setattr(poketracker_main.time, "monotonic", lambda: now["value"])
    signal = target_signal(
        item_id="target-ascended-heroes-booster-bundle",
        message="html=out_of_stock redsky=unknown (50023/IA:http_403)",
    )

    assert poketracker_main._should_probe_target_stock(signal)
    now["value"] = 120.0
    assert not poketracker_main._should_probe_target_stock(signal)
    now["value"] = 131.0
    assert poketracker_main._should_probe_target_stock(signal)


def test_target_stock_probe_add_to_cart_miss_is_non_alerting_skip() -> None:
    signal = target_signal(
        item_id="target-ascended-heroes-booster-bundle",
        message="html=out_of_stock redsky=unknown (50023/IA:http_403)",
    )
    original_decision = Decision(
        type=DecisionType.WOULD_BUY,
        item=signal.item,
        reason="probe checkout: would buy",
        observed_price=Decimal("31.99"),
        msrp=Decimal("31.99"),
        seller=SellerClassification.RETAILER,
        quantity=2,
        weekly_spend_before=Decimal("0"),
        weekly_spend_after=Decimal("63.98"),
        url=signal.item.url,
    )
    checkout_decision = Decision(
        type=DecisionType.PURCHASE_FAILED,
        item=signal.item,
        reason="purchase request rejected with HTTP 409",
        observed_price=Decimal("31.99"),
        msrp=Decimal("31.99"),
        seller=SellerClassification.RETAILER,
        quantity=2,
        weekly_spend_before=Decimal("0"),
        weekly_spend_after=Decimal("0"),
        url=signal.item.url,
        checkout_status="target_add_to_cart_not_found",
        checkout_message="Target checkout could not find the add_to_cart control",
    )

    result = poketracker_main._suppress_expected_target_probe_miss(original_decision, checkout_decision)

    assert result.type == DecisionType.SKIP
    assert result.weekly_spend_after == Decimal("0")
    assert "stock probe" in result.reason


def target_signal(item_id: str, message: str) -> StockSignal:
    item = WatchlistItem(
        id=item_id,
        name="Target: Ascended Heroes Booster Bundle",
        retailer=Retailer.TARGET,
        url="https://www.target.com/p/example/-/A-95120834",
        type=ProductType.BOOSTER_BUNDLE,
        msrp=Decimal("31.99"),
        max_quantity=2,
        enabled=True,
        sku="95120834",
    )
    return StockSignal(
        item=item,
        status=SignalStatus.OUT_OF_STOCK,
        seller=SellerClassification.RETAILER,
        seller_name="Target",
        source="page",
        message=message,
    )
