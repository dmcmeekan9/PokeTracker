from __future__ import annotations

import os
from decimal import Decimal

import boto3

from poketracker.models import Decision


class SesNotifier:
    def __init__(
        self,
        sender: str | None = None,
        recipient: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.sender = sender or os.environ["ALERT_SENDER_EMAIL"]
        self.recipient = recipient or os.environ["ALERT_RECIPIENT_EMAIL"]
        self._ses = boto3.client("ses", region_name=region_name or os.environ.get("AWS_REGION", "us-east-1"))

    def send_decision(self, decision: Decision, subject_prefix: str | None = None) -> None:
        subject, body = render_decision_email(decision, subject_prefix=subject_prefix)
        self._ses.send_email(
            Source=self.sender,
            Destination={"ToAddresses": [self.recipient]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )


def _fmt(value: Decimal | None) -> str:
    return "unknown" if value is None else f"${value:.2f}"


def render_decision_email(decision: Decision, subject_prefix: str | None = None) -> tuple[str, str]:
    prefix = f"{subject_prefix} " if subject_prefix else ""
    return f"{prefix}PokeTracker {decision.type.value}: {decision.item.name}", _render_decision(decision)


def _render_decision(decision: Decision) -> str:
    return "\n".join(
        [
            f"Decision: {decision.type.value}",
            f"Reason: {decision.reason}",
            f"URL: {decision.url}",
            "",
            f"Item: {decision.item.name}",
            f"Item ID: {decision.item.id}",
            f"Retailer: {decision.item.retailer.value}",
            f"Type: {decision.item.type.value}",
            f"Observed price: {_fmt(decision.observed_price)}",
            f"Configured MSRP: {_fmt(decision.msrp)}",
            f"Seller classification: {decision.seller.value}",
            f"Quantity: {decision.quantity}",
            f"Weekly spend before: {_fmt(decision.weekly_spend_before)}",
            f"Weekly spend after: {_fmt(decision.weekly_spend_after)}",
            f"Timestamp: {decision.timestamp.isoformat()}",
        ]
    )
