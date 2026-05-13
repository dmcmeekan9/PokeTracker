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
    parser.add_argument(
        "--verify-url",
        help="Optional Target product URL to open after sign-in so you can clear any challenge before upload.",
    )
    parser.add_argument(
        "--browser-channel",
        choices=["chromium", "chrome", "msedge"],
        default="chromium",
        help="Browser channel to use for the manual session capture.",
    )
    parser.add_argument(
        "--cdp-url",
        help="Connect to an already-open Chrome/Edge remote debugging URL and capture its current Target session.",
    )
    args = parser.parse_args()

    try:
        if args.cdp_url:
            storage_state = capture_target_session_from_cdp(args.output, args.cdp_url, verify_url=args.verify_url)
        else:
            storage_state = capture_target_session(args.output, verify_url=args.verify_url, browser_channel=args.browser_channel)
    except RuntimeError as exc:
        print(f"target session capture failed:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.secret_id:
        encoding = upload_storage_state_secret(storage_state, args.secret_id, args.region)
        print(f"target session uploaded ({encoding})")
    else:
        print(f"target session saved to {args.output}")


def capture_target_session(
    output_path: str | Path,
    verify_url: str | None = None,
    browser_channel: str = "chromium",
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required. Install it with: python -m pip install playwright") from exc

    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {"headless": False}
        if browser_channel != "chromium":
            launch_options["channel"] = browser_channel
        browser = playwright.chromium.launch(**launch_options)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.target.com/account", wait_until="domcontentloaded", timeout=60000)
        print("Sign in to Target in the browser window, confirm your default shipping/payment, then return here.")
        input("Press Enter after the Target account page shows you are signed in...")
        if verify_url:
            page.goto(verify_url, wait_until="domcontentloaded", timeout=60000)
            print("Target preflight page opened.")
            print("Clear any CAPTCHA/challenge, confirm the page shows the account/cart state you expect, then return here.")
            input("Press Enter after the Target product/cart page is usable without intervention...")
        storage_state = context.storage_state()
        browser.close()

    write_storage_state(output_path, storage_state)
    return storage_state


def capture_target_session_from_cdp(
    output_path: str | Path,
    cdp_url: str,
    verify_url: str | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required. Install it with: python -m pip install playwright") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            if verify_url:
                page.goto(verify_url, wait_until="domcontentloaded", timeout=60000)
                print("Target preflight page opened in the attached browser.")
            print("Use the attached browser to clear any Target prompts and confirm the page/cart state is usable.")
            input("Press Enter after Target is usable without intervention...")
            storage_state = context.storage_state()
        finally:
            browser.close()

    write_storage_state(output_path, storage_state)
    return storage_state


def write_storage_state(output_path: str | Path, storage_state: dict[str, Any]) -> None:
    Path(output_path).write_text(json.dumps(storage_state, indent=2), encoding="utf-8")


def upload_storage_state_secret(storage_state: dict[str, Any], secret_id: str, region: str) -> str:
    client = boto3.client("secretsmanager", region_name=region)
    secret_string = encode_storage_state_for_secret(storage_state)
    client.put_secret_value(SecretId=secret_id, SecretString=secret_string)
    if secret_string.startswith(SECRET_ENCODING_PREFIX):
        return "gzip+base64 encoded"
    return "plain JSON"


if __name__ == "__main__":
    main()
