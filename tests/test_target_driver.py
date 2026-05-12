from __future__ import annotations

from poketracker.checkout_webhook.handler_types import CheckoutWebhookError
from poketracker.checkout_webhook.target_driver import _set_target_quantity


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
