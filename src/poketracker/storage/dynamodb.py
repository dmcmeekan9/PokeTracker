from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

from poketracker.models import (
    Decision,
    DecisionType,
    GlobalConfig,
    Retailer,
    ProductType,
    SellerClassification,
    SignalStatus,
    StockSignal,
    WatchlistConfig,
    WatchlistItem,
)


def _decimal_to_wire(value: Decimal) -> str:
    return format(value, "f")


class DynamoStore:
    def __init__(
        self,
        config_table: str | None = None,
        audit_table: str | None = None,
        state_table: str | None = None,
    ) -> None:
        self._dynamodb = boto3.resource("dynamodb")
        self.config = self._dynamodb.Table(config_table or os.environ["CONFIG_TABLE_NAME"])
        self.audit = self._dynamodb.Table(audit_table or os.environ["AUDIT_TABLE_NAME"])
        self.state = self._dynamodb.Table(state_table or os.environ["STATE_TABLE_NAME"])

    def put_config(self, config: WatchlistConfig) -> None:
        desired_keys = {f"ITEM#{item.id}" for item in config.items}
        self.config.put_item(
            Item={
                "pk": "GLOBAL",
                "sk": "CONFIG",
                "purchasing_enabled": config.global_config.purchasing_enabled,
                "weekly_spend_cap": _decimal_to_wire(config.global_config.weekly_spend_cap),
                "timezone": config.global_config.timezone,
            }
        )
        for item in config.items:
            self.config.put_item(Item=self._item_to_record(item))
        self._delete_stale_items(desired_keys)

    def load_config(self) -> WatchlistConfig:
        global_response = self.config.get_item(Key={"pk": "GLOBAL", "sk": "CONFIG"})
        if "Item" not in global_response:
            raise RuntimeError("global config not found in DynamoDB")

        global_record = global_response["Item"]
        scan_response = self.config.scan(FilterExpression=Attr("pk").begins_with("ITEM#"))
        item_records = scan_response.get("Items", [])
        while "LastEvaluatedKey" in scan_response:
            scan_response = self.config.scan(
                FilterExpression=Attr("pk").begins_with("ITEM#"),
                ExclusiveStartKey=scan_response["LastEvaluatedKey"],
            )
            item_records.extend(scan_response.get("Items", []))

        return WatchlistConfig(
            global_config=GlobalConfig(
                purchasing_enabled=bool(global_record["purchasing_enabled"]),
                weekly_spend_cap=Decimal(str(global_record["weekly_spend_cap"])),
                timezone=str(global_record["timezone"]),
            ),
            items=[self._record_to_item(record) for record in item_records],
        )

    def record_signal(self, signal: StockSignal) -> None:
        self._put_audit(
            signal.item.id,
            "signal",
            {
                "status": signal.status.value,
                "observed_price": _decimal_to_wire(signal.observed_price) if signal.observed_price else None,
                "seller": signal.seller.value,
                "seller_name": signal.seller_name,
                "source": signal.source,
                "message": signal.message,
                "checked_at": signal.checked_at.isoformat(),
            },
        )

    def record_decision(self, decision: Decision) -> None:
        self._put_audit(
            decision.item.id,
            "decision",
            {
                "decision_type": decision.type.value,
                "reason": decision.reason,
                "observed_price": _decimal_to_wire(decision.observed_price) if decision.observed_price else None,
                "msrp": _decimal_to_wire(decision.msrp),
                "seller": decision.seller.value,
                "quantity": decision.quantity,
                "weekly_spend_before": _decimal_to_wire(decision.weekly_spend_before),
                "weekly_spend_after": _decimal_to_wire(decision.weekly_spend_after),
                "url": decision.url,
                "timestamp": decision.timestamp.isoformat(),
            },
        )

    def weekly_purchase_spend(self, week_start_iso: str) -> Decimal:
        # V1 is dry-run only, so this remains zero until real purchases are recorded.
        _ = week_start_iso
        return Decimal("0")

    def should_send_alert(self, decision: Decision, cooldown_seconds: int) -> bool:
        key = {"pk": decision.alert_key, "sk": "ALERT"}
        response = self.state.get_item(Key=key)
        now = int(time.time())
        last_sent_at = int(response.get("Item", {}).get("last_sent_at", 0))
        if now - last_sent_at < cooldown_seconds:
            return False
        self.state.put_item(
            Item={
                **key,
                "last_sent_at": now,
                "decision_type": decision.type.value,
                "item_id": decision.item.id,
            }
        )
        return True

    def _put_audit(self, item_id: str, event_type: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        ttl = int(now.timestamp()) + 90 * 24 * 60 * 60
        self.audit.put_item(
            Item={
                "pk": f"ITEM#{item_id}",
                "sk": f"{now.isoformat()}#{uuid.uuid4()}",
                "event_type": event_type,
                "ttl": ttl,
                "payload": _drop_none(payload),
            }
        )

    def _delete_stale_items(self, desired_keys: set[str]) -> None:
        scan_response = self.config.scan(
            ProjectionExpression="pk, sk",
            FilterExpression=Attr("pk").begins_with("ITEM#"),
        )
        records = scan_response.get("Items", [])
        while "LastEvaluatedKey" in scan_response:
            scan_response = self.config.scan(
                ProjectionExpression="pk, sk",
                FilterExpression=Attr("pk").begins_with("ITEM#"),
                ExclusiveStartKey=scan_response["LastEvaluatedKey"],
            )
            records.extend(scan_response.get("Items", []))

        with self.config.batch_writer() as batch:
            for record in records:
                if record["pk"] not in desired_keys:
                    batch.delete_item(Key={"pk": record["pk"], "sk": record["sk"]})

    @staticmethod
    def _item_to_record(item: WatchlistItem) -> dict[str, Any]:
        return {
            "pk": f"ITEM#{item.id}",
            "sk": "METADATA",
            "id": item.id,
            "name": item.name,
            "retailer": item.retailer.value,
            "url": item.url,
            "type": item.type.value,
            "msrp": _decimal_to_wire(item.msrp),
            "max_quantity": item.max_quantity,
            "enabled": item.enabled,
            "sku": item.sku,
            "purchased": item.purchased,
        }

    @staticmethod
    def _record_to_item(record: dict[str, Any]) -> WatchlistItem:
        return WatchlistItem(
            id=str(record["id"]),
            name=str(record["name"]),
            retailer=Retailer(str(record["retailer"])),
            url=str(record["url"]),
            type=ProductType(str(record["type"])),
            msrp=Decimal(str(record["msrp"])),
            max_quantity=int(record["max_quantity"]),
            enabled=bool(record["enabled"]),
            sku=str(record["sku"]) if record.get("sku") else None,
            purchased=record.get("purchased") if isinstance(record.get("purchased"), dict) else None,
        )


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
