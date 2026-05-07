from __future__ import annotations

import argparse
from decimal import Decimal

from poketracker.config.watchlist import load_watchlist_file
from poketracker.models import SellerClassification, SignalStatus, StockSignal
from poketracker.notify.email import SesNotifier, render_decision_email
from poketracker.rules.engine import RulesEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or send a sample PokeTracker alert email.")
    parser.add_argument("--file", default="watchlist.yaml", help="Path to watchlist YAML.")
    parser.add_argument("--item-id", required=True, help="Watchlist item id to preview.")
    parser.add_argument("--weekly-spend-before", default="0", help="Weekly spend before this candidate purchase.")
    parser.add_argument("--send-test", action="store_true", help="Send the preview through SES with a TEST subject prefix.")
    args = parser.parse_args()

    config = load_watchlist_file(args.file)
    items = {item.id: item for item in config.items}
    if args.item_id not in items:
        raise SystemExit(f"item not found: {args.item_id}")

    item = items[args.item_id]
    signal = StockSignal(
        item=item,
        status=SignalStatus.IN_STOCK,
        observed_price=item.msrp,
        seller=SellerClassification.RETAILER,
        seller_name=item.retailer.value,
        source="preview",
        message="preview in-stock signal",
    )
    decision = RulesEngine(config.global_config).evaluate(signal, Decimal(args.weekly_spend_before))
    subject, body = render_decision_email(decision, subject_prefix="[TEST]" if args.send_test else None)

    print(f"Subject: {subject}")
    print()
    print(body)

    if args.send_test:
        SesNotifier().send_decision(decision, subject_prefix="[TEST]")
        print()
        print("Sent test email.")


if __name__ == "__main__":
    main()
