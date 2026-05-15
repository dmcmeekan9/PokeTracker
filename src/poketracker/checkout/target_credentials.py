from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import boto3


@dataclass(frozen=True)
class TargetCredentials:
    username: str
    password: str


def decode_target_credentials_secret(secret_string: str | None) -> TargetCredentials | None:
    if not secret_string or not secret_string.strip():
        return None
    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Target credentials secret must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Target credentials secret must be a JSON object")

    username = _first_string(payload, "username", "email", "login")
    password = _first_string(payload, "password")
    if not username or not password:
        raise ValueError("Target credentials secret must contain non-empty username and password fields")
    return TargetCredentials(username=username, password=password)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload Target login credentials to AWS Secrets Manager.")
    parser.add_argument(
        "--secret-id",
        default=os.environ.get("TARGET_CREDENTIALS_SECRET_ARN") or os.environ.get("TARGET_CREDENTIALS_SECRET_ID"),
        help="Secrets Manager secret id or ARN. Defaults to TARGET_CREDENTIALS_SECRET_ARN.",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="AWS region.")
    parser.add_argument("--username", default=os.environ.get("TARGET_USERNAME"), help="Target account email or phone.")
    args = parser.parse_args()

    if not args.secret_id:
        print("target credentials upload failed: --secret-id or TARGET_CREDENTIALS_SECRET_ARN is required", file=sys.stderr)
        sys.exit(2)

    username = args.username or input("Target username/email/phone: ").strip()
    password = getpass.getpass("Target password: ")
    credentials = TargetCredentials(username=username, password=password)
    secret_string = json.dumps(
        {"username": credentials.username, "password": credentials.password},
        separators=(",", ":"),
    )
    decode_target_credentials_secret(secret_string)

    client = boto3.client("secretsmanager", region_name=args.region)
    client.put_secret_value(SecretId=args.secret_id, SecretString=secret_string)
    print(f"target credentials uploaded to {args.secret_id}")


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


if __name__ == "__main__":
    main()
