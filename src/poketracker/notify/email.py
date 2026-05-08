from __future__ import annotations

import html
import os
from decimal import Decimal

import boto3

from poketracker.models import Decision, DecisionType


DEFAULT_FOOTER_GIF_URL = "https://www.gifcen.com/wp-content/uploads/2023/03/-8.gif"


class SesNotifier:
    def __init__(
        self,
        sender: str | None = None,
        recipient: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.sender = sender or os.environ["ALERT_SENDER_EMAIL"]
        self.recipient = recipient or os.environ["ALERT_RECIPIENT_EMAIL"]
        self.footer_gif_url = os.environ.get("EMAIL_FOOTER_GIF_URL") or DEFAULT_FOOTER_GIF_URL
        self._ses = boto3.client("ses", region_name=region_name or os.environ.get("AWS_REGION", "us-east-1"))

    def send_decision(self, decision: Decision, subject_prefix: str | None = None) -> None:
        subject, text_body, html_body = render_decision_email(
            decision,
            subject_prefix=subject_prefix,
            footer_gif_url=self.footer_gif_url,
        )
        self._ses.send_email(
            Source=self.sender,
            Destination={"ToAddresses": [self.recipient]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )


def _fmt(value: Decimal | None) -> str:
    return "unknown" if value is None else f"${value:.2f}"


def _decision_label(decision: Decision) -> str:
    if decision.type == DecisionType.WOULD_BUY:
        return "BUY THIS!"
    return decision.type.value


def render_decision_email(
    decision: Decision,
    subject_prefix: str | None = None,
    footer_gif_url: str | None = None,
) -> tuple[str, str, str]:
    prefix = f"{subject_prefix} " if subject_prefix else ""
    decision_label = _decision_label(decision)
    separator = " " if decision.type == DecisionType.WOULD_BUY else ": "
    subject = f"{prefix}PokeTracker {decision_label}{separator}{decision.item.name}"
    return subject, _render_text_decision(decision), _render_html_decision(decision, footer_gif_url=footer_gif_url)


def _render_text_decision(decision: Decision) -> str:
    return "\n".join(
        [
            f"Decision: {_decision_label(decision)}",
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


def _render_html_decision(decision: Decision, footer_gif_url: str | None = None) -> str:
    escaped_target_url = html.escape(decision.url, quote=True)
    escaped_decision_label = html.escape(_decision_label(decision))
    fields = [
        ("Item", decision.item.name),
        ("Item ID", decision.item.id),
        ("Retailer", decision.item.retailer.value),
        ("Type", decision.item.type.value),
        ("Observed price", _fmt(decision.observed_price)),
        ("Configured MSRP", _fmt(decision.msrp)),
        ("Seller classification", decision.seller.value),
        ("Quantity", str(decision.quantity)),
        ("Weekly spend before", _fmt(decision.weekly_spend_before)),
        ("Weekly spend after", _fmt(decision.weekly_spend_after)),
        ("Timestamp", decision.timestamp.isoformat()),
    ]
    rows = "\n".join(
        f"""
        <tr>
          <td style="padding:8px 12px;color:#5f6368;font-weight:700;width:180px;">{html.escape(label)}</td>
          <td style="padding:8px 12px;color:#202124;">{html.escape(value)}</td>
        </tr>
        """
        for label, value in fields
    )
    footer_gif = ""
    if footer_gif_url:
        escaped_footer_gif_url = html.escape(footer_gif_url, quote=True)
        footer_gif = f"""
        <div style="padding:18px 0 2px;text-align:center;">
          <img src="{escaped_footer_gif_url}" alt="PokeTracker" style="max-width:260px;width:100%;height:auto;border:0;border-radius:8px;" />
        </div>
        """

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;color:#202124;">
    <div style="max-width:640px;margin:0 auto;padding:24px;">
      <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
        <div style="padding:22px 24px;background:#29233a;color:#ffffff;">
          <div style="font-size:12px;line-height:1.2;text-transform:uppercase;letter-spacing:.08em;color:#c9c2ff;font-weight:700;">PokeTracker Alert</div>
          <div style="font-size:24px;line-height:1.25;font-weight:800;margin-top:8px;">{escaped_decision_label}</div>
        </div>
        <div style="padding:24px;">
          <p style="margin:0 0 12px;font-size:15px;line-height:1.45;"><strong>Reason:</strong> {html.escape(decision.reason)}</p>
          <p style="margin:0 0 20px;">
            <a href="{escaped_target_url}" style="display:inline-block;background:#d9272e;color:#ffffff;text-decoration:none;font-weight:800;border-radius:8px;padding:12px 18px;">Open Target Page</a>
          </p>
          <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;border:1px solid #edf0f3;border-radius:8px;overflow:hidden;">
            {rows}
          </table>
          {footer_gif}
        </div>
      </div>
    </div>
  </body>
</html>"""
