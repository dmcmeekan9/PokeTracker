from __future__ import annotations

from decimal import Decimal

from poketracker.models import SignalStatus
from poketracker.signals.page import _extract_price, _extract_status


def test_disabled_add_to_cart_button_is_out_of_stock() -> None:
    html = '<button type="button" disabled="">Add to cart</button>'

    assert _extract_status(html) == SignalStatus.OUT_OF_STOCK


def test_enabled_add_to_cart_button_is_in_stock() -> None:
    html = '<button type="button">Add to cart</button>'

    assert _extract_status(html) == SignalStatus.IN_STOCK


def test_ignores_generic_free_shipping_price() -> None:
    html = '<meta content="Free standard shipping with $35 orders.">'

    assert _extract_price(html) is None


def test_extracts_structured_price() -> None:
    html = '{"current_retail":59.99}'

    assert _extract_price(html) == Decimal("59.99")
