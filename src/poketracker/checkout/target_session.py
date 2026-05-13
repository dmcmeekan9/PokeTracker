from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3

from poketracker.checkout.target_storage_state import SECRET_ENCODING_PREFIX, encode_storage_state_for_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture and upload a manually authenticated Target browser session.")
    parser.add_argument("--output", default="target-session.json", help="Local storage-state JSON path.")
    parser.add_argument(
        "--secret-id",
        default=os.environ.get("TARGET_SESSION_SECRET_ARN") or os.environ.get("TARGET_SESSION_SECRET_ID"),
        help="Optional Secrets Manager secret id or ARN to upload after capture.",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"), help="AWS region.")
    args = parser.parse_args()

    try:
        storage_state = capture_target_session(args.output)
    except RuntimeError as exc:
        print(f"target session capture failed:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.secret_id:
        client = boto3.client("secretsmanager", region_name=args.region)
        secret_string = encode_storage_state_for_secret(storage_state)
        client.put_secret_value(SecretId=args.secret_id, SecretString=secret_string)
        encoding = "gzip+base64 encoded" if secret_string.startswith(SECRET_ENCODING_PREFIX) else "plain JSON"
        print(f"target session uploaded ({encoding})")
    else:
        print(f"target session saved to {args.output}")


def capture_target_session(output_path: str | Path) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required. Install it with: python -m pip install playwright") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.target.com/account", wait_until="domcontentloaded", timeout=60000)
        print("Sign in to Target in the browser window, confirm your default shipping/payment, then return here.")
        input("Press Enter after the Target account page shows you are signed in...")
        storage_state = context.storage_state()
        browser.close()

    Path(output_path).write_text(json.dumps(storage_state, indent=2), encoding="utf-8")
    return storage_state


if __name__ == "__main__":
    main()
