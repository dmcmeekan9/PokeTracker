from __future__ import annotations

import pytest

from poketracker.checkout.target_storage_state import decode_storage_state_secret, encode_storage_state_for_secret
from poketracker.checkout.target_credentials import TargetCredentials
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError
from poketracker.checkout_webhook.target_driver import (
    _click_first_with_auto_login,
    _dismiss_target_overlays,
    _page_indicates_cart_has_item,
    _set_target_quantity,
    _stop_on_intervention,
    _verify_click_candidate_present,
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


def test_payment_selector_with_place_order_is_not_intervention() -> None:
    _stop_on_intervention(
        """
        <main>
          Select payment type
          Visa *9521
          Save and continue
          By ordering, you accept Target's terms and privacy policy
          Place your order
        </main>
        """
    )


def test_detects_item_already_in_cart() -> None:
    assert _page_indicates_cart_has_item('<span aria-label="cart">1 in cart</span>')
    assert not _page_indicates_cart_has_item("<main>Your cart is empty</main>")


class VisibleControl:
    clicked = False
    waited = False

    @property
    def first(self) -> "VisibleControl":
        return self

    def wait_for(self, state: str, timeout: int) -> None:
        assert state == "visible"
        assert timeout == 1000
        self.waited = True

    def click(self, *args, **kwargs) -> None:
        _ = args
        _ = kwargs
        self.clicked = True


class PageWithVisiblePlaceOrder:
    def __init__(self) -> None:
        self.control = VisibleControl()

    def get_by_role(self, role: str, name) -> VisibleControl:
        _ = role
        if getattr(name, "pattern", "") in {r"continue shopping", r"close"}:
            return MissingControl()
        return self.control

    def get_by_text(self, pattern) -> VisibleControl:
        if getattr(pattern, "pattern", "") in {r"continue shopping", r"close"}:
            return MissingControl()
        return self.control


def test_verify_click_candidate_present_does_not_click() -> None:
    page = PageWithVisiblePlaceOrder()

    _verify_click_candidate_present(page, [r"place order"], "place_order")

    assert page.control.waited is True
    assert page.control.clicked is False


def test_click_first_with_auto_login_recovers_sign_in(monkeypatch) -> None:
    recovered = {}

    def sign_in_required(*_args, **_kwargs):
        raise CheckoutWebhookError(409, "sign_in_required", "Target checkout requires intervention: sign_in_required")

    monkeypatch.setattr("poketracker.checkout_webhook.target_driver._click_first", sign_in_required)
    monkeypatch.setattr(
        "poketracker.checkout_webhook.target_driver._ensure_target_signed_in",
        lambda page, credentials: recovered.update({"credentials": credentials}),
    )

    credentials = TargetCredentials(username="target@example.com", password="password")

    assert _click_first_with_auto_login(object(), [r"checkout"], "checkout", credentials, optional=True) is True
    assert recovered == {"credentials": credentials}


class MissingControl:
    @property
    def first(self) -> "MissingControl":
        return self

    def click(self, *args, **kwargs) -> None:
        _ = args
        _ = kwargs
        raise RuntimeError("missing")


class PageWithTargetOverlay:
    def __init__(self) -> None:
        self.continue_button = VisibleControl()
        self.load_states: list[str] = []
        self.waited = False

    def get_by_role(self, role: str, name) -> VisibleControl | MissingControl:
        _ = name
        if role == "button":
            return self.continue_button
        return MissingControl()

    def get_by_text(self, pattern) -> MissingControl:
        _ = pattern
        return MissingControl()

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        _ = timeout
        self.load_states.append(state)

    def wait_for_timeout(self, timeout: int) -> None:
        assert timeout == 250
        self.waited = True


def test_dismiss_target_overlay_clicks_continue_button() -> None:
    page = PageWithTargetOverlay()

    _dismiss_target_overlays(page)

    assert page.continue_button.clicked is True
    assert page.load_states == ["domcontentloaded"]
    assert page.waited is True


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
