from __future__ import annotations

import json

from poketracker.checkout_webhook import handler


def event(payload: dict, token: str = "secret") -> dict:
    return {
        "headers": {"authorization": f"Bearer {token}"},
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }


def payload() -> dict:
    return {
        "item": {
            "id": "target-ascended-heroes-etb",
            "name": "Target: Ascended Heroes Elite Trainer Box",
            "retailer": "target",
            "type": "ETB",
            "sku": "95082118",
            "url": "https://www.target.com/p/example/-/A-95082118",
        },
        "quantity": 1,
        "observed_price": "59.99",
        "msrp": "59.99",
        "weekly_spend_before": "0",
        "weekly_spend_after": "59.99",
        "decision_timestamp": "2026-05-08T03:00:00+00:00",
    }


def profile() -> dict:
    return {
        "payment": {
            "method_type": "saved_retailer_payment",
            "retailer_account": "target",
            "payment_method_ref": "default",
        }
    }


def test_rejects_missing_token(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setattr(handler, "_load_secret", lambda secret_arn: "secret" if secret_arn == "token-secret" else None)

    response = handler.lambda_handler(event(payload(), token="wrong"), None)

    assert response["statusCode"] == 401
    assert json.loads(response["body"])["status"] == "unauthorized"


def test_rejects_price_above_msrp(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setattr(handler, "_load_secret", lambda secret_arn: "secret")
    raw = payload()
    raw["observed_price"] = "69.99"

    response = handler.lambda_handler(event(raw), None)

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["status"] == "price_above_msrp"


def test_target_fails_when_session_secret_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")

    def load_secret(secret_arn):
        if secret_arn == "token-secret":
            return "secret"
        if secret_arn == "profile-secret":
            return json.dumps(profile())
        return None

    monkeypatch.setattr(handler, "_load_secret", load_secret)

    response = handler.lambda_handler(event(payload()), None)

    assert response["statusCode"] == 503
    assert json.loads(response["body"])["status"] == "target_session_missing"


def test_target_returns_driver_result(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")

    def load_secret(secret_arn):
        if secret_arn == "token-secret":
            return "secret"
        if secret_arn == "profile-secret":
            return json.dumps(profile())
        if secret_arn == "session-secret":
            return json.dumps({"cookies": [], "origins": []})
        return None

    class Result:
        status = "ordered"
        order_id = "ABC123"
        message = "Target checkout completed"

    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "purchase_target_item", lambda *args, **kwargs: Result())

    response = handler.lambda_handler(event(payload()), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["status"] == "ordered"
    assert body["order_id"] == "ABC123"
