from __future__ import annotations

import logging
import os

import boto3

from poketracker.checkout.dry_run import DryRunCheckoutAdapter
from poketracker.models import DecisionType, Retailer, WatchlistItem
from poketracker.notify.email import SesNotifier
from poketracker.rules.engine import RulesEngine, current_week_start_iso
from poketracker.signals.base import SignalAdapter
from poketracker.signals.bestbuy import BestBuyApiSignalAdapter
from poketracker.signals.page import RetailerPageSignalAdapter
from poketracker.storage.dynamodb import DynamoStore

LOGGER = logging.getLogger(__name__)
ALERT_COOLDOWN_SECONDS = 6 * 60 * 60


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    bestbuy_api_key = _load_secret(os.environ.get("BESTBUY_API_KEY_SECRET_ARN"))
    store = DynamoStore()
    notifier = SesNotifier()
    checkout = DryRunCheckoutAdapter()

    config = store.load_config()
    engine = RulesEngine(config.global_config)
    adapters = _build_adapters(bestbuy_api_key)
    week_start = current_week_start_iso(config.global_config.timezone)

    enabled_items = [item for item in config.items if item.enabled]
    LOGGER.info("checking %s enabled items", len(enabled_items))

    for item in enabled_items:
        adapter = adapters.get(item.retailer)
        if adapter is None:
            LOGGER.warning("no adapter for retailer=%s item_id=%s", item.retailer.value, item.id)
            continue
        try:
            signal = adapter.check(item)
            store.record_signal(signal)
            weekly_spend = store.weekly_purchase_spend(week_start)
            decision = engine.evaluate(signal, weekly_spend)
            decision = checkout.execute(decision)
            store.record_decision(decision)
        except Exception:
            LOGGER.exception("item failed softly: %s", item.id)
            continue

        if decision.type in {DecisionType.WOULD_BUY, DecisionType.FYI_ONLY, DecisionType.ERROR}:
            if store.should_send_alert(decision, ALERT_COOLDOWN_SECONDS):
                notifier.send_decision(decision)
                LOGGER.info("sent %s alert for %s", decision.type.value, item.id)
            else:
                LOGGER.info("alert suppressed by cooldown for %s", decision.alert_key)


def _build_adapters(bestbuy_api_key: str | None) -> dict[Retailer, SignalAdapter]:
    page_adapter = RetailerPageSignalAdapter()
    return {
        Retailer.BESTBUY: BestBuyApiSignalAdapter(api_key=bestbuy_api_key),
        Retailer.TARGET: page_adapter,
        Retailer.WALMART: page_adapter,
    }


def _load_secret(secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    response = client.get_secret_value(SecretId=secret_arn)
    return response.get("SecretString")


if __name__ == "__main__":
    main()
