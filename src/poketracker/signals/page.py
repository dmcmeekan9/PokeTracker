from __future__ import annotations

import random
import re
import time
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import quote

import requests

from poketracker.models import SellerClassification, SignalStatus, StockSignal, WatchlistItem
from poketracker.signals.base import SignalAdapter

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


class RetailerPageSignalAdapter(SignalAdapter):
    def __init__(
        self,
        timeout_seconds: int = 10,
        max_attempts: int = 2,
        retry_delay_seconds: float = 1.0,
        request_jitter_seconds: float = 1.0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.request_jitter_seconds = request_jitter_seconds

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

        if response.status_code == 429:
            return StockSignal(
                item=item,
                status=SignalStatus.UNKNOWN,
                source="page",
                message="HTTP 429 rate-limited",
            )

        if response.status_code != 200:
            return StockSignal(
                item=item,
                status=SignalStatus.ERROR,
                source="page",
                message=f"HTTP {response.status_code}",
            )

        html_status = _extract_status(response.text)
        status = html_status
        body = response.text.lower()

        seller, seller_name = _classify_seller(item, body)
        observed_price = _extract_price(response.text)
        redsky_status = SignalStatus.UNKNOWN
        if item.retailer.value == "target" and item.sku:
            redsky_status = self._target_fulfillment_status(item, response.text)
            if redsky_status == SignalStatus.IN_STOCK:
                status = SignalStatus.IN_STOCK
            elif redsky_status == SignalStatus.OUT_OF_STOCK and html_status != SignalStatus.IN_STOCK:
                status = SignalStatus.OUT_OF_STOCK
        if observed_price is None and item.retailer.value == "target" and status == SignalStatus.IN_STOCK:
            observed_price = item.msrp

        if item.retailer.value == "target" and item.sku:
            message = f"html={html_status.value} redsky={redsky_status.value}"
        else:
            message = f"html={html_status.value}"

        return StockSignal(
            item=item,
            status=status,
            observed_price=observed_price,
            seller=seller,
            seller_name=seller_name,
            source="page",
            message=message,
        )

    def _get(self, url: str) -> requests.Response:
        if self.request_jitter_seconds > 0:
            time.sleep(random.uniform(0, self.request_jitter_seconds))
        last_exc: requests.RequestException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.get(url, headers=_BROWSER_HEADERS, timeout=self.timeout_seconds)
                if response.status_code == 429 and attempt < self.max_attempts:
                    time.sleep(self.retry_delay_seconds)
                    continue
                return response
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    time.sleep(self.retry_delay_seconds)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("page request was not attempted")

    def _target_fulfillment_status(self, item: WatchlistItem, html: str) -> SignalStatus:
        api_key = _extract_target_redsky_api_key(html)
        visitor_id = _extract_target_visitor_id(html)
        if not api_key:
            return SignalStatus.UNKNOWN

        params = {
            "tcin": item.sku,
            "store_id": "1767",
            "zip": "50023",
            "state": "IA",
            "latitude": "41.73",
            "longitude": "-93.58",
            "scheduled_delivery_store_id": "1767",
            "pricing_store_id": "1767",
            "has_pricing_store_id": "true",
            "channel": "WEB",
            "page": f"/p/A-{item.sku}",
        }
        if visitor_id:
            params["visitor_id"] = visitor_id

        try:
            response = requests.get(
                "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1",
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "x-api-key": api_key,
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                timeout=self.timeout_seconds,
            )
            if response.status_code != 200:
                return SignalStatus.UNKNOWN
            payload = response.json()
        except (ValueError, requests.RequestException):
            return SignalStatus.UNKNOWN

        return _extract_target_fulfillment_status(payload)


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
    for button_match in re.finditer(r"<button\b(?P<attrs>[^>]*)>(?P<label>.*?)</button>", text, re.IGNORECASE | re.DOTALL):
        attrs = button_match.group("attrs").lower()
        label = _strip_tags(unescape(button_match.group("label"))).strip().lower()
        if label in {"add to cart", "add for shipping", "ship it"}:
            return SignalStatus.OUT_OF_STOCK if "disabled" in attrs else SignalStatus.IN_STOCK

    if any(marker in lower_text for marker in ["add for shipping", "ship it"]):
        return SignalStatus.IN_STOCK
    if any(marker in lower_text for marker in ["out of stock", "sold out", "currently unavailable"]):
        return SignalStatus.OUT_OF_STOCK
    return SignalStatus.UNKNOWN


def _extract_target_redsky_api_key(text: str) -> str | None:
    patterns = [
        r'\\"redsky\\":\{\\"baseUrl\\":\\"https://redsky\.target\.com\\".*?\\"apiKey\\":\\"([^"\\]+)\\"',
        r'"redsky":\{"baseUrl":"https://redsky\.target\.com".*?"apiKey":"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _extract_target_visitor_id(text: str) -> str | None:
    patterns = [
        r'\\"visitor_id\\":\\"([^"\\]+)\\"',
        r'"visitor_id":"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return quote(match.group(1), safe="")
    return None


def _extract_target_fulfillment_status(payload: dict) -> SignalStatus:
    product = payload.get("data", {}).get("product", {})
    fulfillment = product.get("fulfillment", {}) if isinstance(product, dict) else {}
    if not isinstance(fulfillment, dict):
        return SignalStatus.UNKNOWN

    shipping = fulfillment.get("shipping_options", {})
    if isinstance(shipping, dict) and _availability_is_in_stock(shipping.get("availability_status")):
        return SignalStatus.IN_STOCK

    for store in fulfillment.get("store_options", []) or []:
        if not isinstance(store, dict):
            continue
        for key in ("order_pickup", "in_store_only", "ship_to_store"):
            option = store.get(key, {})
            if isinstance(option, dict) and _availability_is_in_stock(option.get("availability_status")):
                return SignalStatus.IN_STOCK

    if fulfillment.get("sold_out") is True or fulfillment.get("is_out_of_stock_in_all_store_locations") is True:
        return SignalStatus.OUT_OF_STOCK

    statuses: list[str] = []
    if isinstance(shipping, dict) and shipping.get("availability_status"):
        statuses.append(str(shipping["availability_status"]))
    for store in fulfillment.get("store_options", []) or []:
        if not isinstance(store, dict):
            continue
        for key in ("order_pickup", "in_store_only", "ship_to_store"):
            option = store.get(key, {})
            if isinstance(option, dict) and option.get("availability_status"):
                statuses.append(str(option["availability_status"]))
    if statuses and all(status.upper() in {"OUT_OF_STOCK", "UNAVAILABLE"} for status in statuses):
        return SignalStatus.OUT_OF_STOCK

    return SignalStatus.UNKNOWN


def _availability_is_in_stock(value: object) -> bool:
    return isinstance(value, str) and value.upper() in {"IN_STOCK", "LIMITED_STOCK", "AVAILABLE"}


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
