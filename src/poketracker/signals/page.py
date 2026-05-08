from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation
from html import unescape

import requests

from poketracker.models import SellerClassification, SignalStatus, StockSignal, WatchlistItem
from poketracker.signals.base import SignalAdapter


class RetailerPageSignalAdapter(SignalAdapter):
    def __init__(self, timeout_seconds: int = 10, max_attempts: int = 2, retry_delay_seconds: float = 1.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    def check(self, item: WatchlistItem) -> StockSignal:
        response = None
        try:
            response = self._get(item.url)
        except (requests.Timeout, requests.ConnectionError) as exc:
            return StockSignal(
                item=item,
                status=SignalStatus.UNKNOWN,
                source="page",
                message=f"transient network failure after {self.max_attempts} attempts: {exc}",
            )
        except requests.RequestException as exc:
            return StockSignal(item=item, status=SignalStatus.ERROR, source="page", message=str(exc))

        if response.status_code != 200:
            return StockSignal(
                item=item,
                status=SignalStatus.ERROR,
                source="page",
                message=f"HTTP {response.status_code}",
            )

        status = _extract_status(response.text)
        body = response.text.lower()

        seller, seller_name = _classify_seller(item, body)
        observed_price = _extract_price(response.text)
        if observed_price is None and item.retailer.value == "target" and status == SignalStatus.IN_STOCK:
            observed_price = item.msrp

        return StockSignal(
            item=item,
            status=status,
            observed_price=observed_price,
            seller=seller,
            seller_name=seller_name,
            source="page",
            message="page check completed",
        )

    def _get(self, url: str) -> requests.Response:
        last_exc: requests.RequestException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return requests.get(url, timeout=self.timeout_seconds)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay_seconds)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("page request was not attempted")


def _extract_price(text: str) -> Decimal | None:
    price_patterns = [
        r'"current_retail"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)',
        r'"salePrice"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)',
        r'"regularPrice"\s*:\s*([0-9]+(?:\.[0-9]{1,2})?)',
        r'"formatted_current_price"\s*:\s*"\$\s*([0-9]+(?:\.[0-9]{2})?)"',
    ]
    match = None
    for pattern in price_patterns:
        match = re.search(pattern, text)
        if match:
            break
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def _extract_status(text: str) -> SignalStatus:
    lower_text = text.lower()
    if any(marker in lower_text for marker in ["out of stock", "sold out", "currently unavailable"]):
        return SignalStatus.OUT_OF_STOCK

    for button_match in re.finditer(r"<button\b(?P<attrs>[^>]*)>(?P<label>.*?)</button>", text, re.IGNORECASE | re.DOTALL):
        attrs = button_match.group("attrs").lower()
        label = _strip_tags(unescape(button_match.group("label"))).strip().lower()
        if label == "add to cart":
            return SignalStatus.OUT_OF_STOCK if "disabled" in attrs else SignalStatus.IN_STOCK

    if any(marker in lower_text for marker in ["add for shipping", "ship it"]):
        return SignalStatus.IN_STOCK
    return SignalStatus.UNKNOWN


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def _classify_seller(item: WatchlistItem, body: str) -> tuple[SellerClassification, str | None]:
    retailer_name = {
        "target": "Target",
        "walmart": "Walmart",
        "bestbuy": "Best Buy",
    }.get(item.retailer.value)

    if retailer_name and f"sold by {retailer_name.lower()}" in body:
        return SellerClassification.RETAILER, retailer_name
    if "sold by" in body:
        return SellerClassification.THIRD_PARTY, "third-party"

    # Target-owned PDPs often omit an explicit "sold by Target" string in the
    # server HTML. Target Plus marketplace pages should expose a seller marker.
    if item.retailer.value == "target" and "target plus" not in body:
        return SellerClassification.RETAILER, "Target"

    return SellerClassification.UNKNOWN, None
