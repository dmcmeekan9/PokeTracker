from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class CheckoutWebhookError(ValueError):
    def __init__(self, status_code: int, status: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.status = status
        self.message = message


@dataclass(frozen=True)
class PurchaseRequest:
    item_id: str
    item_name: str
    retailer: str
    sku: str | None
    url: str
    quantity: int
    observed_price: Decimal
    msrp: Decimal
