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


def test_accepts_quantity_two(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")
    raw = payload()
    raw["quantity"] = 2
    raw["weekly_spend_after"] = "119.98"
    captured = []

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
        quantity = 2

    def purchase_target_item(request, *_args, **_kwargs):
        captured.append(request)
        return Result()

    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "purchase_target_item", purchase_target_item)

    response = handler.lambda_handler(event(raw), None)

    assert response["statusCode"] == 200
    assert captured[0].quantity == 2
    assert json.loads(response["body"])["quantity"] == 2


def test_rejects_quantity_over_supported_limit(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setattr(handler, "_load_secret", lambda secret_arn: "secret")
    raw = payload()
    raw["quantity"] = 3

    response = handler.lambda_handler(event(raw), None)

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["status"] == "unsupported_quantity"


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
        quantity = 1

    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "purchase_target_item", lambda *args, **kwargs: Result())

    response = handler.lambda_handler(event(payload()), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["status"] == "ordered"
    assert body["order_id"] == "ABC123"
    assert body["quantity"] == 1


def test_target_verify_only_returns_ready_without_order(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")
    captured = {}

    def load_secret(secret_arn):
        if secret_arn == "token-secret":
            return "secret"
        if secret_arn == "profile-secret":
            return json.dumps(profile())
        if secret_arn == "session-secret":
            return json.dumps({"cookies": [], "origins": []})
        return None

    class Result:
        status = "ready_to_place_order"
        order_id = None
        message = "Target checkout reached the final place-order control; no order was placed"
        quantity = 1

    def purchase_target_item(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    raw = payload()
    raw["verify_only"] = True
    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "purchase_target_item", purchase_target_item)

    response = handler.lambda_handler(event(raw), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["status"] == "ready_to_place_order"
    assert body["order_id"] is None
    assert captured["verify_only"] is True


def test_target_uses_cdp_browser_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")
    monkeypatch.setenv("TARGET_CDP_URL", "http://10.42.0.10:9222")
    captured = {}

    def load_secret(secret_arn):
        if secret_arn == "token-secret":
            return "secret"
        if secret_arn == "profile-secret":
            return json.dumps(profile())
        if secret_arn == "session-secret":
            return json.dumps({"cookies": [], "origins": []})
        raise AssertionError(f"unexpected secret load: {secret_arn}")

    class Result:
        status = "ready_to_place_order"
        order_id = None
        message = "Target checkout reached the final place-order control; no order was placed"
        quantity = 1

    def purchase_target_item_from_cdp(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Result()

    raw = payload()
    raw["verify_only"] = True
    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "purchase_target_item_from_cdp", purchase_target_item_from_cdp)

    response = handler.lambda_handler(event(raw), None)

    assert response["statusCode"] == 200
    assert captured["args"][0] == "http://10.42.0.10:9222"
    assert captured["kwargs"]["verify_only"] is True
    assert captured["kwargs"]["place_order_enabled"] is False


def test_target_passes_credentials_to_cdp_browser(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_CREDENTIALS_SECRET_ARN", "credentials-secret")
    monkeypatch.setenv("TARGET_CDP_URL", "http://10.42.0.10:9222")
    captured = {}

    def load_secret(secret_arn):
        if not secret_arn:
            return None
        if secret_arn == "token-secret":
            return "secret"
        if secret_arn == "profile-secret":
            return json.dumps(profile())
        if secret_arn == "credentials-secret":
            return json.dumps({"username": "target@example.com", "password": "password"})
        raise AssertionError(f"unexpected secret load: {secret_arn}")

    class Result:
        status = "ready_to_place_order"
        order_id = None
        message = "Target checkout reached the final place-order control; no order was placed"
        quantity = 1

    def purchase_target_item_from_cdp(*args, **kwargs):
        _ = args
        captured.update(kwargs)
        return Result()

    raw = payload()
    raw["verify_only"] = True
    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "purchase_target_item_from_cdp", purchase_target_item_from_cdp)

    response = handler.lambda_handler(event(raw), None)

    assert response["statusCode"] == 200
    assert captured["target_credentials"].username == "target@example.com"


def test_rejects_non_boolean_verify_only(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setattr(handler, "_load_secret", lambda secret_arn: "secret")
    raw = payload()
    raw["verify_only"] = "true"

    response = handler.lambda_handler(event(raw), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["status"] == "bad_request"


def test_target_persists_refreshed_session_state(monkeypatch) -> None:
    monkeypatch.setenv("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN", "token-secret")
    monkeypatch.setenv("CHECKOUT_PROFILE_SECRET_ARN", "profile-secret")
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")
    stored = {}

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
        quantity = 1
        storage_state = {"cookies": [{"name": "session", "value": "fresh"}], "origins": []}

    monkeypatch.setattr(handler, "_load_secret", load_secret)
    monkeypatch.setattr(handler, "_store_target_session", lambda storage_state: stored.update(storage_state))
    monkeypatch.setattr(handler, "purchase_target_item", lambda *args, **kwargs: Result())

    response = handler.lambda_handler(event(payload()), None)

    assert response["statusCode"] == 200
    assert stored == Result.storage_state
