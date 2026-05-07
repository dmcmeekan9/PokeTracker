from __future__ import annotations

import os
from decimal import Decimal

import requests

from poketracker.models import SellerClassification, SignalStatus, StockSignal, WatchlistItem
from poketracker.signals.base import SignalAdapter


class BestBuyApiSignalAdapter(SignalAdapter):
    def __init__(self, api_key: str | None = None, timeout_seconds: int = 10) -> None:
        self.api_key = api_key or os.environ.get("BESTBUY_API_KEY")
        self.timeout_seconds = timeout_seconds

    def check(self, item: WatchlistItem) -> StockSignal:
        if not item.sku:
            return StockSignal(
                item=item,
                status=SignalStatus.ERROR,
                source="bestbuy_api",
                message="Best Buy item is missing sku",
            )
        if not self.api_key:
            return StockSignal(
                item=item,
                status=SignalStatus.ERROR,
                source="bestbuy_api",
                message="BESTBUY_API_KEY is not configured",
            )

        url = f"https://api.bestbuy.com/v1/products/{item.sku}.json"
        params = {
            "apiKey": self.api_key,
            "show": "sku,name,salePrice,regularPrice,onlineAvailability,url",
        }
        try:
            response = requests.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            return StockSignal(
                item=item,
                status=SignalStatus.ERROR,
                source="bestbuy_api",
                message=str(exc),
            )

        online_available = bool(data.get("onlineAvailability"))
        price = data.get("salePrice") or data.get("regularPrice")
        observed_price = Decimal(str(price)) if price is not None else None

        return StockSignal(
            item=item,
            status=SignalStatus.IN_STOCK if online_available else SignalStatus.OUT_OF_STOCK,
            observed_price=observed_price,
            seller=SellerClassification.RETAILER,
            seller_name="Best Buy",
            source="bestbuy_api",
            message=data.get("name"),
        )
