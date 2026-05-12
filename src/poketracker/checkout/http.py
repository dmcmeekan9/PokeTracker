from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import requests

from poketracker.checkout.base import CheckoutAdapter
from poketracker.models import Decision, DecisionType


class HttpCheckoutAdapter(CheckoutAdapter):
    def __init__(
        self,
        webhook_url: str,
        bearer_token: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self.webhook_url = webhook_url
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def execute(self, decision: Decision) -> Decision:
        if decision.type != DecisionType.WOULD_BUY:
            return decision

        try:
            response = requests.post(
                self.webhook_url,
                json=_decision_payload(decision),
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            return _failed(decision, f"purchase request failed: {exc}", "request_error", str(exc))

        message = response.text.strip()[:500] or None
        data = _json_or_empty(response)
        if not 200 <= response.status_code < 300:
            return _failed(
                decision,
                f"purchase request rejected with HTTP {response.status_code}",
                str(response.status_code),
                message,
            )

        order_id = _first_str(data, "order_id", "orderId", "confirmation_number", "confirmationNumber")
        status = _first_str(data, "status") or "accepted"
        checkout_message = _first_str(data, "message") or message
        quantity = _first_int(data, "quantity", "quantity_purchased", "purchased_quantity") or decision.quantity
        weekly_spend_after = decision.weekly_spend_after
        if decision.observed_price is not None:
            weekly_spend_after = decision.weekly_spend_before + (decision.observed_price * quantity)
        return replace(
            decision,
            type=DecisionType.PURCHASED,
            reason="purchased: checkout webhook accepted the order request",
            quantity=quantity,
            weekly_spend_after=weekly_spend_after,
            checkout_status=status,
            checkout_order_id=order_id,
            checkout_message=checkout_message,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers


def _decision_payload(decision: Decision) -> dict[str, Any]:
    return {
        "item": {
            "id": decision.item.id,
            "name": decision.item.name,
            "retailer": decision.item.retailer.value,
            "type": decision.item.type.value,
            "sku": decision.item.sku,
            "url": decision.item.url,
        },
        "quantity": decision.quantity,
        "observed_price": _decimal_to_str(decision.observed_price),
        "msrp": _decimal_to_str(decision.msrp),
        "weekly_spend_before": _decimal_to_str(decision.weekly_spend_before),
        "weekly_spend_after": _decimal_to_str(decision.weekly_spend_after),
        "decision_timestamp": decision.timestamp.isoformat(),
    }


def _decimal_to_str(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value)
    return None


def _first_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _failed(decision: Decision, reason: str, status: str, message: str | None) -> Decision:
    return replace(
        decision,
        type=DecisionType.PURCHASE_FAILED,
        reason=reason,
        weekly_spend_after=decision.weekly_spend_before,
        checkout_status=status,
        checkout_message=message,
    )
