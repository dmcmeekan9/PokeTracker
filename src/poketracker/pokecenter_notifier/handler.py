from __future__ import annotations

import html as html_module
import os
from typing import Any

import boto3

from poketracker.models import Retailer, SignalStatus, WatchlistItem
from poketracker.pokecenter_notifier.checker import check_item
from poketracker.storage.dynamodb import DynamoStore


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    store = DynamoStore()
    config = store.load_config()
    items = [i for i in config.items if i.retailer == Retailer.POKEMONCENTER and i.enabled]
    if not items:
        return {"statusCode": 200, "body": "no pokemoncenter items enabled"}

    ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    sender = os.environ["ALERT_SENDER_EMAIL"]
    recipient = os.environ["ALERT_RECIPIENT_EMAIL"]

    notified: list[str] = []
    for item in items:
        signal = check_item(item)

        state_key = {"pk": f"POKECENTER_STATUS#{item.id}", "sk": "STOCK_STATE"}
        prev = store.state.get_item(Key=state_key).get("Item", {})
        last_status = prev.get("status", SignalStatus.UNKNOWN.value)

        store.state.put_item(Item={**state_key, "status": signal.status.value})

        if signal.status == SignalStatus.IN_STOCK and last_status != SignalStatus.IN_STOCK.value:
            _send_alert(ses, sender, recipient, item)
            notified.append(item.id)

    return {"statusCode": 200, "body": f"checked {len(items)} item(s), notified: {notified}"}


def _send_alert(ses: Any, sender: str, recipient: str, item: WatchlistItem) -> None:
    escaped_url = html_module.escape(item.url, quote=True)
    escaped_name = html_module.escape(item.name)
    subject = f"[PokeTracker] IN STOCK: {item.name}"
    text_body = f"{item.name} is IN STOCK at Pokémon Center.\n\n{item.url}\n\n— PokeTracker"
    html_body = f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;">
    <div style="max-width:560px;margin:0 auto;padding:24px;">
      <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden;">
        <div style="padding:20px 24px;background:#e3350d;color:#fff;">
          <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;opacity:.85;font-weight:700;">PokeTracker Alert</div>
          <div style="font-size:22px;font-weight:800;margin-top:6px;">IN STOCK</div>
        </div>
        <div style="padding:24px;">
          <p style="margin:0 0 18px;font-size:16px;font-weight:700;">{escaped_name}</p>
          <a href="{escaped_url}" style="display:inline-block;background:#e3350d;color:#fff;text-decoration:none;font-weight:800;border-radius:8px;padding:12px 20px;font-size:15px;">Buy Now at Pokémon Center</a>
        </div>
      </div>
    </div>
  </body>
</html>"""
    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )
