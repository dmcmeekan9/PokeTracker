from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Retailer(StrEnum):
    TARGET = "target"
    WALMART = "walmart"
    BESTBUY = "bestbuy"


class ProductType(StrEnum):
    ETB = "ETB"
    BOOSTER_BUNDLE = "Booster Bundle"


class SellerClassification(StrEnum):
    RETAILER = "retailer"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


class SignalStatus(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"
    ERROR = "error"


class DecisionType(StrEnum):
    WOULD_BUY = "WOULD_BUY"
    FYI_ONLY = "FYI_ONLY"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass(frozen=True)
class GlobalConfig:
    purchasing_enabled: bool
    weekly_spend_cap: Decimal
    timezone: str


@dataclass(frozen=True)
class WatchlistItem:
    id: str
    name: str
    retailer: Retailer
    url: str
    type: ProductType
    msrp: Decimal
    max_quantity: int
    enabled: bool
    sku: str | None = None
    purchased: dict[str, Any] | None = None


@dataclass(frozen=True)
class WatchlistConfig:
    global_config: GlobalConfig
    items: list[WatchlistItem]


@dataclass(frozen=True)
class StockSignal:
    item: WatchlistItem
    status: SignalStatus
    observed_price: Decimal | None = None
    seller: SellerClassification = SellerClassification.UNKNOWN
    seller_name: str | None = None
    source: str = "unknown"
    message: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Decision:
    type: DecisionType
    item: WatchlistItem
    reason: str
    observed_price: Decimal | None
    msrp: Decimal
    seller: SellerClassification
    quantity: int
    weekly_spend_before: Decimal
    weekly_spend_after: Decimal
    url: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def alert_key(self) -> str:
        return f"{self.item.id}#{self.type.value}"
