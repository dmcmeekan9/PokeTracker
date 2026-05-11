from __future__ import annotations

import pytest

from poketracker.checkout.profile import CheckoutProfileValidationError, validate_checkout_profile


def profile() -> dict:
    return {
        "contact": {
            "email": "buyer@example.com",
            "phone": "+15555550123",
        },
        "shipping_address": {
            "name": "Buyer Name",
            "line1": "123 Main St",
            "line2": "Apt 4",
            "city": "Chicago",
            "state": "IL",
            "postal_code": "60601",
            "country": "US",
        },
        "payment": {
            "method_type": "saved_retailer_payment",
            "retailer_account": "target",
            "payment_method_ref": "default",
        },
    }


def test_accepts_saved_retailer_payment_profile() -> None:
    validate_checkout_profile(profile())


def test_accepts_payment_token_profile() -> None:
    raw = profile()
    raw["payment"] = {
        "method_type": "payment_token",
        "token_ref": "tok_saved_payment_123",
    }

    validate_checkout_profile(raw)


def test_rejects_raw_card_number() -> None:
    raw = profile()
    raw["payment"]["card_number"] = "4111111111111111"

    with pytest.raises(CheckoutProfileValidationError, match="raw payment field"):
        validate_checkout_profile(raw)


def test_rejects_raw_cvv_nested_anywhere() -> None:
    raw = profile()
    raw["payment"]["metadata"] = {"cvv": "123"}

    with pytest.raises(CheckoutProfileValidationError, match="raw payment field"):
        validate_checkout_profile(raw)


def test_requires_shipping_address() -> None:
    raw = profile()
    raw["shipping_address"]["postal_code"] = ""

    with pytest.raises(CheckoutProfileValidationError, match="shipping_address.postal_code"):
        validate_checkout_profile(raw)
