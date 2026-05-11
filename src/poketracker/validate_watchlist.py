from __future__ import annotations

import argparse
import os
import sys

from poketracker.config.watchlist import (
    WatchlistValidationError,
    load_watchlist_file,
    validate_enabled_urls,
    validate_purchasing_ready,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate PokeTracker watchlist YAML.")
    parser.add_argument("--file", default="watchlist.yaml", help="Path to watchlist YAML.")
    parser.add_argument(
        "--skip-url-check",
        action="store_true",
        help="Validate schema only; do not require enabled URLs to return HTTP 200.",
    )
    parser.add_argument(
        "--checkout-webhook-url",
        default=os.environ.get("CHECKOUT_WEBHOOK_URL"),
        help="Checkout webhook URL required when purchasing is enabled.",
    )
    parser.add_argument(
        "--managed-checkout-webhook",
        action="store_true",
        default=os.environ.get("POKETRACKER_MANAGED_CHECKOUT_WEBHOOK", "").lower() in {"1", "true", "yes"},
        help="Allow purchasing readiness to pass because Terraform will create the managed checkout webhook.",
    )
    args = parser.parse_args()

    try:
        config = load_watchlist_file(args.file)
        validate_purchasing_ready(config, args.checkout_webhook_url, args.managed_checkout_webhook)
        if not args.skip_url_check:
            validate_enabled_urls(config)
    except WatchlistValidationError as exc:
        print(f"watchlist validation failed:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"watchlist validation passed: {len(config.items)} items")


if __name__ == "__main__":
    main()
