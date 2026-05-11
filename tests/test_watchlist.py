from __future__ import annotations

import pytest

from poketracker.config.watchlist import WatchlistValidationError, parse_watchlist, validate_purchasing_ready


def base_watchlist() -> dict:
    return {
        "global": {
            "purchasing_enabled": True,
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

    assert config.global_config.purchasing_enabled is True
    assert config.global_config.weekly_spend_cap == 150
    assert config.items[0].id == "target-sample-etb"


def test_parse_booster_bundle_alias() -> None:
    raw = base_watchlist()
    raw["items"][0]["type"] = "BB"

    config = parse_watchlist(raw)

    assert config.items[0].type.value == "Booster Bundle"


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


def test_purchasing_enabled_requires_checkout_webhook_url() -> None:
    config = parse_watchlist(base_watchlist())

    with pytest.raises(WatchlistValidationError, match="CHECKOUT_WEBHOOK_URL"):
        validate_purchasing_ready(config, None)


def test_purchasing_readiness_accepts_managed_checkout_webhook() -> None:
    config = parse_watchlist(base_watchlist())

    validate_purchasing_ready(config, None, managed_checkout_webhook_enabled=True)


def test_purchasing_enabled_requires_https_checkout_webhook_url() -> None:
    config = parse_watchlist(base_watchlist())

    with pytest.raises(WatchlistValidationError, match="https URL"):
        validate_purchasing_ready(config, "http://checkout.example.com/purchase")


def test_purchasing_readiness_accepts_https_checkout_webhook_url() -> None:
    config = parse_watchlist(base_watchlist())

    validate_purchasing_ready(config, "https://checkout.example.com/purchase")
