from __future__ import annotations

import json

from poketracker.checkout_webhook import session_refresh
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError


def test_refresh_updates_session_secret(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")
    monkeypatch.setenv("TARGET_SESSION_VERIFY_URL", "https://www.target.com/p/example")
    stored = {}

    class Result:
        status = "refreshed"
        message = "Target session refreshed in AWS"
        storage_state = {"cookies": [{"name": "session", "value": "fresh"}], "origins": []}

    monkeypatch.setattr(session_refresh, "_load_secret", lambda secret_arn: json.dumps({"cookies": [], "origins": []}))
    monkeypatch.setattr(session_refresh, "_store_target_session", lambda storage_state: stored.update(storage_state))
    monkeypatch.setattr(session_refresh, "refresh_target_session", lambda *args, **kwargs: Result())

    response = session_refresh.lambda_handler({}, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["status"] == "refreshed"
    assert stored == Result.storage_state


def test_refresh_returns_checkout_error(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_SESSION_SECRET_ARN", "session-secret")
    monkeypatch.setattr(session_refresh, "_load_secret", lambda secret_arn: None)

    def fail(*_args, **_kwargs):
        raise CheckoutWebhookError(503, "target_session_missing", "Target session secret is not configured")

    monkeypatch.setattr(session_refresh, "refresh_target_session", fail)

    response = session_refresh.lambda_handler({}, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 503
    assert body["status"] == "target_session_missing"
