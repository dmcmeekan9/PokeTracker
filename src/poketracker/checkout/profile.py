from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3


RAW_PAYMENT_FIELD_NAMES = {
    "card",
    "card_number",
    "cardnumber",
    "cc",
    "cc_number",
    "ccnumber",
    "credit_card",
    "creditcard",
    "cvv",
    "cvc",
    "security_code",
    "expiration",
    "expiry",
    "exp_month",
    "exp_year",
}


class CheckoutProfileValidationError(ValueError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a safe checkout profile to AWS Secrets Manager.")
    parser.add_argument("--file", required=True, help="Path to checkout profile JSON.")
    parser.add_argument(
        "--secret-id",
        default=os.environ.get("CHECKOUT_PROFILE_SECRET_ARN") or os.environ.get("CHECKOUT_PROFILE_SECRET_ID"),
        help="Secrets Manager secret id or ARN. Defaults to CHECKOUT_PROFILE_SECRET_ARN.",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="AWS region.")
    args = parser.parse_args()

    if not args.secret_id:
        print("checkout profile upload failed: --secret-id or CHECKOUT_PROFILE_SECRET_ARN is required", file=sys.stderr)
        raise SystemExit(1)

    try:
        profile = load_checkout_profile(args.file)
    except CheckoutProfileValidationError as exc:
        print(f"checkout profile validation failed:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    client = boto3.client("secretsmanager", region_name=args.region)
    client.put_secret_value(SecretId=args.secret_id, SecretString=json.dumps(profile, separators=(",", ":")))
    print("checkout profile uploaded")


def load_checkout_profile(path: str | Path) -> dict[str, Any]:
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckoutProfileValidationError(f"could not read checkout profile JSON: {exc}") from exc
    validate_checkout_profile(profile)
    return profile


def validate_checkout_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        raise CheckoutProfileValidationError("profile must be a JSON object")
    _reject_raw_payment_fields(profile)

    contact = _require_object(profile, "contact")
    shipping = _require_object(profile, "shipping_address")
    payment = _require_object(profile, "payment")

    _require_nonempty_str(contact, "email", "contact.email")
    _require_nonempty_str(contact, "phone", "contact.phone")

    for field in ["name", "line1", "city", "state", "postal_code", "country"]:
        _require_nonempty_str(shipping, field, f"shipping_address.{field}")

    method_type = _require_nonempty_str(payment, "method_type", "payment.method_type")
    if method_type not in {"saved_retailer_payment", "payment_token"}:
        raise CheckoutProfileValidationError("payment.method_type must be saved_retailer_payment or payment_token")

    if method_type == "saved_retailer_payment":
        _require_nonempty_str(payment, "retailer_account", "payment.retailer_account")
        _require_nonempty_str(payment, "payment_method_ref", "payment.payment_method_ref")
    else:
        _require_nonempty_str(payment, "token_ref", "payment.token_ref")


def _reject_raw_payment_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = key.lower().replace("-", "_").replace(" ", "_")
            if normalized_key in RAW_PAYMENT_FIELD_NAMES:
                raise CheckoutProfileValidationError(f"raw payment field is not allowed: {path}.{key}")
            _reject_raw_payment_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_payment_fields(child, f"{path}[{index}]")


def _require_object(raw: dict[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise CheckoutProfileValidationError(f"{field} must be an object")
    return value


def _require_nonempty_str(raw: dict[str, Any], field: str, label: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CheckoutProfileValidationError(f"{label} must be a non-empty string")
    return value


if __name__ == "__main__":
    main()
