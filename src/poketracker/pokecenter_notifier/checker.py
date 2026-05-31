from __future__ import annotations

import json
import random
import re
import time

from poketracker.models import SellerClassification, SignalStatus, StockSignal, WatchlistItem

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.pokemoncenter.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def check_item(item: WatchlistItem, timeout_seconds: int = 15, jitter_seconds: float = 2.0) -> StockSignal:
    if jitter_seconds > 0:
        time.sleep(random.uniform(0, jitter_seconds))
    try:
        from curl_cffi import requests as cffi_requests
        response = cffi_requests.get(
            item.url,
            headers=_HEADERS,
            impersonate="chrome120",
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return StockSignal(
            item=item,
            status=SignalStatus.UNKNOWN,
            source="pokecenter_page",
            message=f"request failed: {exc}",
        )

    if response.status_code == 429:
        return StockSignal(item=item, status=SignalStatus.UNKNOWN, source="pokecenter_page", message="HTTP 429 rate-limited")

    if response.status_code != 200:
        return StockSignal(item=item, status=SignalStatus.ERROR, source="pokecenter_page", message=f"HTTP {response.status_code}")

    status = _parse_status(response.text)
    return StockSignal(
        item=item,
        status=status,
        seller=SellerClassification.RETAILER,
        seller_name="Pokémon Center",
        source="pokecenter_page",
        message="page check completed",
    )


def _parse_status(html: str) -> SignalStatus:
    lower = html.lower()

    # JSON-LD structured data is most reliable
    for ld_match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(ld_match.group(1))
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                offers = node.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    avail = str(offers.get("availability", ""))
                    if "InStock" in avail:
                        return SignalStatus.IN_STOCK
                    if "OutOfStock" in avail or "SoldOut" in avail:
                        return SignalStatus.OUT_OF_STOCK
        except (json.JSONDecodeError, AttributeError):
            continue

    # Button-based detection
    for btn_match in re.finditer(
        r"<button\b(?P<attrs>[^>]*)>(?P<label>.*?)</button>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        attrs = btn_match.group("attrs").lower()
        label = re.sub(r"<[^>]+>", "", btn_match.group("label")).strip().lower()
        if label == "add to cart":
            return SignalStatus.OUT_OF_STOCK if "disabled" in attrs else SignalStatus.IN_STOCK
        if label in {"sold out", "out of stock"}:
            return SignalStatus.OUT_OF_STOCK

    # Plain-text fallback
    if "add to cart" in lower:
        return SignalStatus.IN_STOCK
    if any(m in lower for m in ("sold out", "out of stock", "currently unavailable", "notify me when available")):
        return SignalStatus.OUT_OF_STOCK

    return SignalStatus.UNKNOWN
