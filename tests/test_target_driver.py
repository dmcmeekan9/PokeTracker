from __future__ import annotations

import pytest

from poketracker.checkout.target_storage_state import decode_storage_state_secret, encode_storage_state_for_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError
from poketracker.checkout_webhook.target_driver import (
    _page_indicates_cart_has_item,
    _set_target_quantity,
    _stop_on_intervention,
    purchase_target_item,
)


class EmptyLocator:
    def count(self) -> int:
        return 0


class PageWithoutQuantityControl:
    def locator(self, selector: str) -> EmptyLocator:
        _ = selector
        return EmptyLocator()


def test_quantity_two_falls_back_to_one_when_control_is_missing(monkeypatch) -> None:
    def missing_control(*_args, **_kwargs):
        raise CheckoutWebhookError(409, "target_quantity_increment_not_found", "missing")

    monkeypatch.setattr("poketracker.checkout_webhook.target_driver._click_first", missing_control)

    assert _set_target_quantity(PageWithoutQuantityControl(), 2) == 1


class SelectLocator:
    selected: str | None = None

    def count(self) -> int:
        return 1

    def nth(self, index: int) -> "SelectLocator":
        assert index == 0
        return self

    def select_option(self, value: str, timeout: int) -> None:
        _ = timeout
        self.selected = value


class PageWithQuantitySelect:
    def __init__(self) -> None:
        self.select = SelectLocator()

    def locator(self, selector: str) -> SelectLocator:
        _ = selector
        return self.select

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        _ = state
        _ = timeout


def test_quantity_two_uses_select_when_available() -> None:
    page = PageWithQuantitySelect()

    assert _set_target_quantity(page, 2) == 2
    assert page.select.selected == "2"


@pytest.mark.parametrize(
    ("html", "status"),
    [
        ("<main>Verify you are human before continuing</main>", "captcha"),
        ("<main>Loading screen Something went wrong Please try again in a bit or use another device.</main>", "target_blocked"),
        ("<main>Enter your password to continue</main>", "sign_in_required"),
        ("<main>We sent a verification code</main>", "identity_verification"),
        ("<main>Enter card security code</main>", "payment_intervention"),
        ("<main>Select a payment method</main>", "payment_intervention"),
        ("<main>Your card was declined</main>", "payment_intervention"),
    ],
)
def test_stops_on_checkout_interventions(html: str, status: str) -> None:
    with pytest.raises(CheckoutWebhookError) as exc_info:
        _stop_on_intervention(html)

    assert exc_info.value.status == status


def test_saved_payment_label_is_not_intervention() -> None:
    _stop_on_intervention("<main>Payment method Visa ending in 4242</main>")


def test_detects_item_already_in_cart() -> None:
    assert _page_indicates_cart_has_item('<span aria-label="cart">1 in cart</span>')
    assert not _page_indicates_cart_has_item("<main>Your cart is empty</main>")


def test_large_target_session_secret_round_trips_with_encoding() -> None:
    storage_state = {
        "cookies": [{"name": "session", "value": "x" * 70000, "domain": ".target.com", "path": "/"}],
        "origins": [{"origin": "https://www.target.com", "localStorage": []}],
    }

    encoded = encode_storage_state_for_secret(storage_state)

    assert encoded.startswith("gzip+base64:")
    assert len(encoded.encode("utf-8")) <= 65536
    assert decode_storage_state_secret(encoded) == storage_state


def test_invalid_target_session_secret_fails_before_driver_import() -> None:
    with pytest.raises(CheckoutWebhookError) as exc_info:
        purchase_target_item(object(), {}, "gzip+base64:not-valid")

    assert exc_info.value.status == "target_session_invalid"
