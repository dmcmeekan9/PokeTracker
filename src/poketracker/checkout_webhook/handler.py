from __future__ import annotations

import base64
import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3

from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest
from poketracker.checkout_webhook.target_driver import purchase_target_item

MAX_PURCHASE_QUANTITY = 2


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = context
    try:
        _authorize(event)
        payload = _event_json(event)
        request = _purchase_request(payload)
        profile = _load_checkout_profile()
        result = _execute_purchase(request, profile)
    except CheckoutWebhookError as exc:
        return _json_response(exc.status_code, {"status": exc.status, "message": exc.message})
    except Exception as exc:
        return _json_response(500, {"status": "error", "message": f"checkout webhook failed: {exc}"})

    return _json_response(200, result)


def _authorize(event: dict[str, Any]) -> None:
    expected = _load_secret(os.environ.get("CHECKOUT_WEBHOOK_TOKEN_SECRET_ARN"))
    if not expected:
        raise CheckoutWebhookError(503, "unconfigured", "checkout webhook token secret is not configured")

    headers = {str(key).lower(): str(value) for key, value in (event.get("headers") or {}).items()}
    authorization = headers.get("authorization", "")
    if authorization != f"Bearer {expected}":
        raise CheckoutWebhookError(401, "unauthorized", "invalid checkout webhook bearer token")


def _event_json(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if not isinstance(body, str) or not body:
        raise CheckoutWebhookError(400, "bad_request", "request body must be JSON")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CheckoutWebhookError(400, "bad_request", f"request body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CheckoutWebhookError(400, "bad_request", "request body must be a JSON object")
    return payload


def _purchase_request(payload: dict[str, Any]) -> PurchaseRequest:
    item = payload.get("item")
    if not isinstance(item, dict):
        raise CheckoutWebhookError(400, "bad_request", "item must be an object")

    quantity = _int(payload.get("quantity"), "quantity")
    if quantity < 1 or quantity > MAX_PURCHASE_QUANTITY:
        raise CheckoutWebhookError(
            409,
            "unsupported_quantity",
            f"checkout webhook only supports quantities 1-{MAX_PURCHASE_QUANTITY}",
        )

    observed_price = _money(payload.get("observed_price"), "observed_price")
    msrp = _money(payload.get("msrp"), "msrp")
    if observed_price > msrp:
        raise CheckoutWebhookError(409, "price_above_msrp", "observed price is above MSRP")

    return PurchaseRequest(
        item_id=_str(item.get("id"), "item.id"),
        item_name=_str(item.get("name"), "item.name"),
        retailer=_str(item.get("retailer"), "item.retailer"),
        sku=str(item["sku"]) if item.get("sku") is not None else None,
        url=_str(item.get("url"), "item.url"),
        quantity=quantity,
        observed_price=observed_price,
        msrp=msrp,
    )


def _load_checkout_profile() -> dict[str, Any]:
    profile = _load_secret(os.environ.get("CHECKOUT_PROFILE_SECRET_ARN"))
    if not profile:
        raise CheckoutWebhookError(503, "unconfigured", "checkout profile secret is not configured")
    try:
        data = json.loads(profile)
    except json.JSONDecodeError as exc:
        raise CheckoutWebhookError(503, "unconfigured", f"checkout profile secret is not valid JSON: {exc}") from exc
    if data.get("configured") is False:
        raise CheckoutWebhookError(503, "unconfigured", "checkout profile secret still contains the placeholder value")
    return data


def _execute_purchase(request: PurchaseRequest, profile: dict[str, Any]) -> dict[str, str | None]:
    if request.retailer != "target":
        raise CheckoutWebhookError(409, "unsupported_retailer", f"retailer is not supported yet: {request.retailer}")

    payment = profile.get("payment") if isinstance(profile, dict) else None
    if not isinstance(payment, dict):
        raise CheckoutWebhookError(503, "unconfigured", "checkout profile payment object is missing")
    if payment.get("method_type") != "saved_retailer_payment":
        raise CheckoutWebhookError(409, "unsupported_payment", "Target checkout requires a saved retailer payment reference")
    if payment.get("retailer_account") != "target":
        raise CheckoutWebhookError(409, "unsupported_payment", "checkout profile payment retailer_account must be target")

    result = purchase_target_item(
        request,
        profile,
        target_session_json=_load_secret(os.environ.get("TARGET_SESSION_SECRET_ARN")),
    )
    return {
        "status": result.status,
        "order_id": result.order_id,
        "message": result.message,
        "quantity": result.quantity,
    }


def _load_secret(secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except client.exceptions.ResourceNotFoundException:
        return None
    return response.get("SecretString")


def _json_response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, separators=(",", ":")),
    }


def _str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckoutWebhookError(400, "bad_request", f"{field} must be a non-empty string")
    return value


def _int(value: Any, field: str) -> int:
    if not isinstance(value, int):
        raise CheckoutWebhookError(400, "bad_request", f"{field} must be an integer")
    return value


def _money(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise CheckoutWebhookError(400, "bad_request", f"{field} must be numeric") from exc
    if parsed <= 0:
        raise CheckoutWebhookError(400, "bad_request", f"{field} must be positive")
    return parsed
