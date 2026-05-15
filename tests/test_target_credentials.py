from __future__ import annotations

import pytest

from poketracker.checkout.target_credentials import decode_target_credentials_secret


def test_decodes_target_credentials_secret() -> None:
    credentials = decode_target_credentials_secret('{"email":"target@example.com","password":"secret"}')

    assert credentials is not None
    assert credentials.username == "target@example.com"
    assert credentials.password == "secret"


def test_empty_target_credentials_secret_is_optional() -> None:
    assert decode_target_credentials_secret(None) is None
    assert decode_target_credentials_secret("") is None


def test_rejects_missing_target_credentials_fields() -> None:
    with pytest.raises(ValueError, match="username and password"):
        decode_target_credentials_secret('{"username":"target@example.com"}')
