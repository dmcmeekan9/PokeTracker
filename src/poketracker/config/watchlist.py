from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from poketracker.models import GlobalConfig, ProductType, Retailer, WatchlistConfig, WatchlistItem


class WatchlistValidationError(ValueError):
    pass


def load_watchlist_file(path: str | Path) -> WatchlistConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return parse_watchlist(raw)


def parse_watchlist(raw: dict[str, Any]) -> WatchlistConfig:
    if not isinstance(raw, dict):
        raise WatchlistValidationError("watchlist must be a YAML mapping")

    global_raw = raw.get("global")
    if not isinstance(global_raw, dict):
        raise WatchlistValidationError("global config is required")

    try:
        global_config = GlobalConfig(
            purchasing_enabled=_require_bool(global_raw, "purchasing_enabled"),
            weekly_spend_cap=_decimal(global_raw.get("weekly_spend_cap"), "global.weekly_spend_cap"),
            timezone=_require_str(global_raw, "timezone"),
        )
    except KeyError as exc:
        raise WatchlistValidationError(f"missing global field: {exc.args[0]}") from exc

    if global_config.weekly_spend_cap <= 0:
        raise WatchlistValidationError("global.weekly_spend_cap must be positive")

    items_raw = raw.get("items")
    if not isinstance(items_raw, list):
        raise WatchlistValidationError("items must be a list")

    items: list[WatchlistItem] = []
    seen_ids: set[str] = set()
    for index, item_raw in enumerate(items_raw):
        if not isinstance(item_raw, dict):
            raise WatchlistValidationError(f"items[{index}] must be a mapping")
        item = _parse_item(item_raw, index)
        if item.id in seen_ids:
            raise WatchlistValidationError(f"duplicate item id: {item.id}")
        seen_ids.add(item.id)
        items.append(item)

    return WatchlistConfig(global_config=global_config, items=items)


def validate_enabled_urls(config: WatchlistConfig, timeout_seconds: int = 10) -> None:
    failures: list[str] = []
    for item in config.items:
        if not item.enabled:
            continue
        try:
            response = requests.get(item.url, timeout=timeout_seconds)
        except requests.RequestException as exc:
            failures.append(f"{item.id}: URL check failed: {exc}")
            continue
        if response.status_code != 200:
            failures.append(f"{item.id}: expected HTTP 200, got {response.status_code}")

    if failures:
        raise WatchlistValidationError("\n".join(failures))


def _parse_item(raw: dict[str, Any], index: int) -> WatchlistItem:
    prefix = f"items[{index}]"
    try:
        item_id = _require_str(raw, "id")
        retailer = Retailer(_require_str(raw, "retailer"))
        product_type = ProductType(_require_str(raw, "type"))
        max_quantity = _require_int(raw, "max_quantity")
        url = _require_str(raw, "url")
        enabled = _require_bool(raw, "enabled")
    except KeyError as exc:
        raise WatchlistValidationError(f"{prefix} missing field: {exc.args[0]}") from exc
    except ValueError as exc:
        raise WatchlistValidationError(f"{prefix}: {exc}") from exc

    if not item_id:
        raise WatchlistValidationError(f"{prefix}.id must not be empty")
    if max_quantity != 1:
        raise WatchlistValidationError(f"{prefix}.max_quantity must be 1")
    _validate_url(url, f"{prefix}.url")

    msrp = _decimal(raw.get("msrp"), f"{prefix}.msrp")
    if msrp <= 0:
        raise WatchlistValidationError(f"{prefix}.msrp must be positive")

    return WatchlistItem(
        id=item_id,
        name=_require_str(raw, "name"),
        retailer=retailer,
        url=url,
        type=product_type,
        msrp=msrp,
        max_quantity=max_quantity,
        enabled=enabled,
        sku=str(raw["sku"]) if raw.get("sku") is not None else None,
        purchased=raw.get("purchased") if isinstance(raw.get("purchased"), dict) else None,
    )


def _require_str(raw: dict[str, Any], field: str) -> str:
    value = raw[field]
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_bool(raw: dict[str, Any], field: str) -> bool:
    value = raw[field]
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_int(raw: dict[str, Any], field: str) -> int:
    value = raw[field]
    if not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise WatchlistValidationError(f"{field} must be numeric") from exc


def _validate_url(url: str, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WatchlistValidationError(f"{field} must be an http(s) URL")
