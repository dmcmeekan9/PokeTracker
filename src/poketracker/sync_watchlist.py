from __future__ import annotations

import argparse

from poketracker.config.watchlist import load_watchlist_file
from poketracker.storage.dynamodb import DynamoStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync watchlist YAML into DynamoDB.")
    parser.add_argument("--file", default="watchlist.yaml", help="Path to watchlist YAML.")
    args = parser.parse_args()

    config = load_watchlist_file(args.file)
    store = DynamoStore()
    store.put_config(config)
    print(f"synced {len(config.items)} watchlist items")


if __name__ == "__main__":
    main()
