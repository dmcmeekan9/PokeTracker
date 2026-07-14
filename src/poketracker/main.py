from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import boto3
from botocore.exceptions import ClientError

from poketracker.checkout.base import CheckoutAdapter
from poketracker.checkout.dry_run import DryRunCheckoutAdapter, UnconfiguredCheckoutAdapter
from poketracker.checkout.http import HttpCheckoutAdapter
from poketracker.models import DecisionType, Retailer, StockSignal, WatchlistItem
from poketracker.models import SellerClassification, SignalStatus
from poketracker.notify.email import SesNotifier
from poketracker.rules.engine import RulesEngine, current_week_start_iso
from poketracker.signals.base import SignalAdapter
from poketracker.signals.bestbuy import BestBuyApiSignalAdapter
from poketracker.signals.page import RetailerPageSignalAdapter
from poketracker.storage.dynamodb import DynamoStore

LOGGER = logging.getLogger(__name__)
ALERT_COOLDOWN_SECONDS = 6 * 60 * 60
_LAST_TARGET_PROBE_AT: dict[str, float] = {}


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    burst_duration_seconds = _optional_positive_int("POKETRACKER_BURST_DURATION_SECONDS")
    burst_interval_seconds = _optional_positive_int("POKETRACKER_BURST_INTERVAL_SECONDS") or 60
    if burst_duration_seconds:
        _run_burst(burst_duration_seconds, burst_interval_seconds)
        return

    run_once()


def _run_burst(duration_seconds: int, interval_seconds: int) -> None:
    deadline = time.monotonic() + duration_seconds
    iteration = 1
    while True:
        LOGGER.info("starting burst iteration %s", iteration)
        run_once()
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= interval_seconds:
            return
        time.sleep(interval_seconds)
        iteration += 1


def run_once() -> None:
    bestbuy_api_key = _load_secret(os.environ.get("BESTBUY_API_KEY_SECRET_ARN"))
    store = DynamoStore()
    notifier = SesNotifier()

    config = store.load_config()
    engine = RulesEngine(config.global_config)
    checkout = _build_checkout(config.global_config.purchasing_enabled)
    adapters = _build_adapters(bestbuy_api_key)
    week_start = current_week_start_iso(config.global_config.timezone)

    enabled_items = [item for item in config.items if item.enabled]
    LOGGER.info("checking %s enabled items", len(enabled_items))

    signals = _fetch_signals(enabled_items, adapters)

    # Evaluate all signals first; collect items that need a checkout attempt.
    pending: list[tuple[WatchlistItem, Any]] = []
    completed: list[tuple[WatchlistItem, Any]] = []
    for item, signal in signals:
        if signal is None:
            continue
        try:
            store.record_signal(signal)
            weekly_spend = store.weekly_purchase_spend(week_start)
            decision = engine.evaluate(signal, weekly_spend)
            if config.global_config.purchasing_enabled and decision.type == DecisionType.WOULD_BUY:
                if store.item_purchased_this_week(item.id, week_start):
                    decision = replace(
                        decision,
                        type=DecisionType.SKIP,
                        reason="item already has a recorded v2 purchase this week",
                        weekly_spend_after=weekly_spend,
                    )
            if (
                config.global_config.purchasing_enabled
                and decision.type == DecisionType.SKIP
                and _should_probe_target_stock(signal)
            ):
                probe_signal = replace(
                    signal,
                    status=SignalStatus.IN_STOCK,
                    observed_price=signal.observed_price or item.msrp,
                    seller=SellerClassification.RETAILER,
                    seller_name=signal.seller_name or "Target",
                    source=f"{signal.source}+target_probe",
                    message=f"{signal.message}; target_stock_probe=enabled",
                )
                probe_decision = engine.evaluate(probe_signal, weekly_spend)
                if probe_decision.type == DecisionType.WOULD_BUY:
                    if store.item_purchased_this_week(item.id, week_start):
                        decision = replace(
                            probe_decision,
                            type=DecisionType.SKIP,
                            reason="item already has a recorded v2 purchase this week",
                            weekly_spend_after=weekly_spend,
                        )
                    else:
                        decision = replace(
                            probe_decision,
                            reason=f"probe checkout: {probe_decision.reason}",
                        )
            if decision.type == DecisionType.WOULD_BUY:
                pending.append((item, decision))
            else:
                completed.append((item, decision))
        except Exception:
            LOGGER.exception("item failed softly: %s", item.id)

    # Execute all checkouts concurrently so simultaneous restocks are all attempted.
    if pending:
        def _execute_checkout(args: tuple[WatchlistItem, Any]) -> tuple[WatchlistItem, Any]:
            item, decision = args
            try:
                return item, _suppress_expected_target_probe_miss(decision, checkout.execute(decision))
            except Exception:
                LOGGER.exception("checkout execute failed softly: %s", item.id)
                return item, decision

        with ThreadPoolExecutor(max_workers=len(pending)) as pool:
            completed.extend(pool.map(_execute_checkout, pending))

    # Record decisions and send alerts.
    for item, decision in completed:
        try:
            store.record_decision(decision)
            if decision.type == DecisionType.PURCHASED:
                store.record_purchase(decision, week_start)
        except Exception:
            LOGGER.exception("item failed softly: %s", item.id)
            continue

        if decision.type in {
            DecisionType.WOULD_BUY,
            DecisionType.PURCHASED,
            DecisionType.PURCHASE_FAILED,
            DecisionType.FYI_ONLY,
            DecisionType.ERROR,
        }:
            if store.should_send_alert(decision, ALERT_COOLDOWN_SECONDS):
                notifier.send_decision(decision)
                LOGGER.info("sent %s alert for %s", decision.type.value, item.id)
            else:
                LOGGER.info("alert suppressed by cooldown for %s", decision.alert_key)


def _fetch_signals(
    items: list[WatchlistItem],
    adapters: dict[Retailer, SignalAdapter],
) -> list[tuple[WatchlistItem, StockSignal | None]]:
    def _check_one(item: WatchlistItem) -> tuple[WatchlistItem, StockSignal | None]:
        adapter = adapters.get(item.retailer)
        if adapter is None:
            LOGGER.warning("no adapter for retailer=%s item_id=%s", item.retailer.value, item.id)
            return item, None
        try:
            return item, adapter.check(item)
        except Exception:
            LOGGER.exception("signal fetch failed softly: %s", item.id)
            return item, None

    with ThreadPoolExecutor(max_workers=len(items) or 1) as pool:
        return list(pool.map(_check_one, items))


def _build_adapters(bestbuy_api_key: str | None) -> dict[Retailer, SignalAdapter]:
    page_adapter = RetailerPageSignalAdapter()
    return {
        Retailer.BESTBUY: BestBuyApiSignalAdapter(api_key=bestbuy_api_key),
        Retailer.TARGET: page_adapter,
        Retailer.WALMART: page_adapter,
    }


def _build_checkout(purchasing_enabled: bool) -> CheckoutAdapter:
    if not purchasing_enabled:
        return DryRunCheckoutAdapter()
    webhook_url = os.environ.get("CHECKOUT_WEBHOOK_URL")
    if not webhook_url:
        LOGGER.warning("purchasing_enabled=true but CHECKOUT_WEBHOOK_URL is not configured")
        return UnconfiguredCheckoutAdapter()

    bearer_token = _load_secret(os.environ.get("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN")) or os.environ.get(
        "CHECKOUT_WEBHOOK_TOKEN"
    )
    return HttpCheckoutAdapter(webhook_url=webhook_url, bearer_token=bearer_token)


def _load_secret(secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"ResourceNotFoundException", "InvalidRequestException"}:
            LOGGER.warning("optional secret is not ready: %s", secret_arn)
            return None
        raise
    return response.get("SecretString")


def _optional_positive_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("%s must be an integer; ignoring value=%r", name, raw)
        return None
    if value <= 0:
        LOGGER.warning("%s must be positive; ignoring value=%r", name, raw)
        return None
    return value


def _should_probe_target_stock(signal: StockSignal) -> bool:
    item = signal.item
    if item.retailer != Retailer.TARGET or signal.status != SignalStatus.OUT_OF_STOCK:
        return False
    if not item.sku or item.id not in _target_stock_probe_item_ids():
        return False
    message = signal.message or ""
    if "redsky=unknown" not in message or "http_403" not in message:
        return False

    cooldown = _optional_positive_int("TARGET_STOCK_PROBE_COOLDOWN_SECONDS") or 30
    now = time.monotonic()
    last_probe_at = _LAST_TARGET_PROBE_AT.get(item.id)
    if last_probe_at is not None and now - last_probe_at < cooldown:
        return False
    _LAST_TARGET_PROBE_AT[item.id] = now
    LOGGER.info("target stock probe enabled for %s after Redsky 403 with stale out-of-stock HTML", item.id)
    return True


def _target_stock_probe_item_ids() -> set[str]:
    raw = os.environ.get("TARGET_STOCK_PROBE_ITEM_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _suppress_expected_target_probe_miss(original_decision: Any, checkout_decision: Any) -> Any:
    if (
        original_decision.reason.startswith("probe checkout:")
        and checkout_decision.type == DecisionType.PURCHASE_FAILED
        and checkout_decision.item.retailer == Retailer.TARGET
        and checkout_decision.checkout_status == "target_add_to_cart_not_found"
    ):
        return replace(
            checkout_decision,
            type=DecisionType.SKIP,
            reason="target stock probe did not find a purchasable add-to-cart control",
            weekly_spend_after=checkout_decision.weekly_spend_before,
        )
    return checkout_decision


if __name__ == "__main__":
    main()
