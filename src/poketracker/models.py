from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


class Retailer(str, Enum):
    TARGET = "target"
    WALMART = "walmart"
    BESTBUY = "bestbuy"
    POKEMONCENTER = "pokemoncenter"


class ProductType(str, Enum):
    ETB = "ETB"
    BOOSTER_BUNDLE = "Booster Bundle"
    POSTER_COLLECTION = "Poster Collection"


PRODUCT_TYPE_ALIASES = {
    "BB": ProductType.BOOSTER_BUNDLE,
    "BOOSTER_BUNDLE": ProductType.BOOSTER_BUNDLE,
    "PC": ProductType.POSTER_COLLECTION,
    "POSTER_COLLECTION": ProductType.POSTER_COLLECTION,
}


def parse_product_type(value: str) -> ProductType:
    if value in PRODUCT_TYPE_ALIASES:
        return PRODUCT_TYPE_ALIASES[value]
    return ProductType(value)


class SellerClassification(str, Enum):
    RETAILER = "retailer"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


class SignalStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"
    ERROR = "error"


class DecisionType(str, Enum):
    WOULD_BUY = "WOULD_BUY"
    PURCHASED = "PURCHASED"
    PURCHASE_FAILED = "PURCHASE_FAILED"
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
    checkout_status: str | None = None
    checkout_order_id: str | None = None
    checkout_message: str | None = None

    @property
    def alert_key(self) -> str:
        return f"{self.item.id}#{self.type.value}"
