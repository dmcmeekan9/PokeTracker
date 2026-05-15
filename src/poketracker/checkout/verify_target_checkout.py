from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

from poketracker.checkout.profile import load_checkout_profile
from poketracker.checkout.target_credentials import decode_target_credentials_secret
from poketracker.checkout.target_storage_state import encode_storage_state_for_secret
from poketracker.checkout.local_target_buyer import purchase_target_item_from_cdp
from poketracker.checkout_webhook.handler_types import CheckoutWebhookError, PurchaseRequest
from poketracker.checkout_webhook.target_driver import purchase_target_item
from poketracker.config.watchlist import load_watchlist_file
from poketracker.models import Retailer


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Target checkout reaches the final place-order control.")
    parser.add_argument("--watchlist", default="watchlist.yaml", help="Watchlist YAML path.")
    parser.add_argument("--profile", default="checkout-profile.json", help="Local checkout profile JSON path.")
    parser.add_argument("--target-session", default="target-session.json", help="Local Target storage-state JSON path.")
    parser.add_argument("--item-id", help="Watchlist item id to verify. Defaults to the first enabled Target item.")
    parser.add_argument("--url", help="Explicit Target product URL to verify instead of reading the watchlist.")
    parser.add_argument("--item-name", default="Target checkout verification item", help="Item name for explicit --url.")
    parser.add_argument("--sku", help="Optional SKU for explicit --url.")
    parser.add_argument("--quantity", type=int, default=1, help="Quantity to prepare.")
    parser.add_argument("--observed-price", default="1.00", help="Observed price for explicit --url.")
    parser.add_argument("--msrp", default="1.00", help="MSRP for explicit --url.")
    parser.add_argument("--debug-output-dir", help="Write checkout page debug artifacts when verification fails.")
    parser.add_argument("--cdp-url", help="Verify through an already-open debug Chrome instead of launching headless.")
    parser.add_argument(
        "--target-credentials-secret-id",
        default=os.environ.get("TARGET_CREDENTIALS_SECRET_ARN") or os.environ.get("TARGET_CREDENTIALS_SECRET_ID"),
        help="Optional Secrets Manager secret id or ARN for Target auto-login during CDP verification.",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="AWS region for secrets.")
    args = parser.parse_args()

    try:
        if args.debug_output_dir:
            os.environ["TARGET_CHECKOUT_DEBUG_DIR"] = args.debug_output_dir
        profile = load_checkout_profile(args.profile)
        request = _request_from_args(args)
        target_credentials = _load_target_credentials(args.target_credentials_secret_id, args.region)
        if args.cdp_url:
            result = purchase_target_item_from_cdp(
                args.cdp_url,
                request,
                profile,
                place_order_enabled=False,
                target_credentials=target_credentials,
                verify_only=True,
            )
        else:
            session_json = Path(args.target_session).read_text(encoding="utf-8")
            result = purchase_target_item(
                request,
                profile,
                encode_storage_state_for_secret(json.loads(session_json)),
                target_credentials=target_credentials,
                verify_only=True,
            )
    except CheckoutWebhookError as exc:
        print(f"target checkout verification failed: {exc.status} - {exc.message}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"target checkout verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(
        json.dumps(
            {
                "status": result.status,
                "message": result.message,
                "quantity": result.quantity,
            },
            separators=(",", ":"),
        )
    )


def _request_from_args(args: Any) -> PurchaseRequest:
    if args.url:
        return PurchaseRequest(
            item_id="target-checkout-verification",
            item_name=args.item_name,
            retailer="target",
            sku=args.sku,
            url=args.url,
            quantity=args.quantity,
            observed_price=Decimal(str(args.observed_price)),
            msrp=Decimal(str(args.msrp)),
        )

    config = load_watchlist_file(args.watchlist)
    for item in config.items:
        if args.item_id and item.id != args.item_id:
            continue
        if item.enabled and item.retailer == Retailer.TARGET:
            return PurchaseRequest(
                item_id=item.id,
                item_name=item.name,
                retailer=item.retailer.value,
                sku=item.sku,
                url=item.url,
                quantity=min(args.quantity, item.max_quantity),
                observed_price=item.msrp,
                msrp=item.msrp,
            )

    if args.item_id:
        raise ValueError(f"no enabled Target watchlist item found for --item-id {args.item_id}")
    raise ValueError("no enabled Target watchlist item found")


def _load_target_credentials(secret_id: str | None, region: str) -> Any:
    if not secret_id:
        return None
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_id)
    return decode_target_credentials_secret(response.get("SecretString"))


if __name__ == "__main__":
    main()
