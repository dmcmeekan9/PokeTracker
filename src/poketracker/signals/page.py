from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import requests

from poketracker.models import SellerClassification, SignalStatus, StockSignal, WatchlistItem
from poketracker.signals.base import SignalAdapter


class RetailerPageSignalAdapter(SignalAdapter):
    def __init__(self, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = timeout_seconds

    def check(self, item: WatchlistItem) -> StockSignal:
        try:
            response = requests.get(item.url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            return StockSignal(item=item, status=SignalStatus.ERROR, source="page", message=str(exc))

        if response.status_code != 200:
            return StockSignal(
                item=item,
                status=SignalStatus.ERROR,
                source="page",
                message=f"HTTP {response.status_code}",
            )

        body = response.text.lower()
        status = SignalStatus.UNKNOWN
        if any(marker in body for marker in ["add to cart", "add for shipping", "ship it"]):
            status = SignalStatus.IN_STOCK
        elif any(marker in body for marker in ["out of stock", "sold out", "currently unavailable"]):
            status = SignalStatus.OUT_OF_STOCK

        seller = SellerClassification.UNKNOWN
        seller_name = None
        retailer_name = {
            "target": "Target",
            "walmart": "Walmart",
            "bestbuy": "Best Buy",
        }.get(item.retailer.value)
        if retailer_name and f"sold by {retailer_name.lower()}" in body:
            seller = SellerClassification.RETAILER
            seller_name = retailer_name
        elif "sold by" in body:
            seller = SellerClassification.THIRD_PARTY
            seller_name = "third-party"

        return StockSignal(
            item=item,
            status=status,
            observed_price=_extract_price(response.text),
            seller=seller,
            seller_name=seller_name,
            source="page",
            message="page check completed",
        )


def _extract_price(text: str) -> Decimal | None:
    match = re.search(r"\$\s*([0-9]+(?:\.[0-9]{2})?)", text)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None
