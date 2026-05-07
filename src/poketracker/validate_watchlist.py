from __future__ import annotations

import argparse
import sys

from poketracker.config.watchlist import WatchlistValidationError, load_watchlist_file, validate_enabled_urls


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate PokeTracker watchlist YAML.")
    parser.add_argument("--file", default="watchlist.yaml", help="Path to watchlist YAML.")
    parser.add_argument(
        "--skip-url-check",
        action="store_true",
        help="Validate schema only; do not require enabled URLs to return HTTP 200.",
    )
    args = parser.parse_args()

    try:
        config = load_watchlist_file(args.file)
        if not args.skip_url_check:
            validate_enabled_urls(config)
    except WatchlistValidationError as exc:
        print(f"watchlist validation failed:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"watchlist validation passed: {len(config.items)} items")


if __name__ == "__main__":
    main()
