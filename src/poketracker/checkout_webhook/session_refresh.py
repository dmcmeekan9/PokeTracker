from __future__ import annotations

import json
import os
from typing import Any

import boto3

from poketracker.checkout.target_storage_state import encode_storage_state_for_secret
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError
from poketracker.checkout_webhook.target_driver import refresh_target_session


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = event
    _ = context
    try:
        result = refresh_target_session(
            _load_secret(os.environ.get("TARGET_SESSION_SECRET_ARN")),
            verify_url=os.environ.get("TARGET_SESSION_VERIFY_URL") or None,
        )
        _store_target_session(result.storage_state)
    except CheckoutWebhookError as exc:
        return _json_response(exc.status_code, {"status": exc.status, "message": exc.message})
    except Exception as exc:
        return _json_response(500, {"status": "error", "message": f"target session refresh failed: {exc}"})

    return _json_response(200, {"status": result.status, "message": result.message})


def _load_secret(secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except client.exceptions.ResourceNotFoundException:
        return None
    return response.get("SecretString")


def _store_target_session(storage_state: dict[str, Any]) -> None:
    secret_arn = os.environ.get("TARGET_SESSION_SECRET_ARN")
    if not secret_arn:
        raise CheckoutWebhookError(503, "target_session_missing", "Target session secret is not configured")
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    client.put_secret_value(
        SecretId=secret_arn,
        SecretString=encode_storage_state_for_secret(storage_state),
    )


def _json_response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, separators=(",", ":")),
    }
