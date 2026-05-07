from __future__ import annotations

import pytest

from poketracker.config.watchlist import WatchlistValidationError, parse_watchlist


def base_watchlist() -> dict:
    return {
        "global": {
            "purchasing_enabled": False,
            "weekly_spend_cap": 150,
            "timezone": "America/Chicago",
        },
        "items": [
            {
                "id": "target-sample-etb",
                "name": "Sample ETB",
                "retailer": "target",
                "url": "https://www.target.com/p/sample",
                "type": "ETB",
                "msrp": 49.99,
                "max_quantity": 1,
                "enabled": True,
            }
        ],
    }


def test_parse_valid_watchlist() -> None:
    config = parse_watchlist(base_watchlist())

    assert config.global_config.weekly_spend_cap == 150
    assert config.items[0].id == "target-sample-etb"


def test_rejects_duplicate_ids() -> None:
    raw = base_watchlist()
    raw["items"].append(dict(raw["items"][0]))

    with pytest.raises(WatchlistValidationError, match="duplicate item id"):
        parse_watchlist(raw)


def test_rejects_quantity_over_one() -> None:
    raw = base_watchlist()
    raw["items"][0]["max_quantity"] = 2

    with pytest.raises(WatchlistValidationError, match="max_quantity must be 1"):
        parse_watchlist(raw)


def test_rejects_unknown_retailer() -> None:
    raw = base_watchlist()
    raw["items"][0]["retailer"] = "pokemoncenter"

    with pytest.raises(WatchlistValidationError):
        parse_watchlist(raw)
