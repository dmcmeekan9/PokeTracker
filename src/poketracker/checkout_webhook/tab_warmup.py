from __future__ import annotations

import json
import os
from typing import Any

import boto3

from poketracker.checkout.target_storage_state import decode_storage_state_secret
from poketracker.checkout_webhook.target_driver import (
    _goto_target_page,
    _new_target_context,
    kill_cdp_service_workers,
    probe_cdp_endpoint,
    restart_cdp_browser_if_configured,
    resolve_cdp_browser_url,
)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    _ = event, context

    cdp_url = os.environ.get("TARGET_CDP_URL")
    if not cdp_url:
        return _response(200, {"status": "skipped", "message": "TARGET_CDP_URL not set"})

    raw_urls = os.environ.get("TARGET_WARMUP_URLS", "")
    urls = [u.strip() for u in raw_urls.split(",") if u.strip()]
    if not urls:
        return _response(200, {"status": "skipped", "message": "TARGET_WARMUP_URLS not configured"})

    target_session_json = _load_secret(os.environ.get("TARGET_SESSION_SECRET_ARN"))
    if not target_session_json:
        return _response(200, {"status": "skipped", "message": "target session not available"})

    try:
        storage_state = decode_storage_state_secret(target_session_json)
    except ValueError as exc:
        return _response(200, {"status": "skipped", "message": f"invalid target session: {exc}"})

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _response(503, {"status": "error", "message": "playwright not installed"})

    warmed: list[str] = []
    failed: list[str] = []
    cdp_probe = probe_cdp_endpoint(cdp_url)

    try:
        restart_cdp_browser_if_configured()
        kill_cdp_service_workers(cdp_url)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(resolve_cdp_browser_url(cdp_url), timeout=10000)
            except Exception as exc:
                return _response(
                    200,
                    {
                        "status": "skipped",
                        "message": f"CDP unavailable (EC2 likely stopped): {exc}",
                        "cdp_probe": cdp_probe,
                    },
                )

            try:
                # Index existing pre-warmed pages by normalized URL.
                existing: dict[str, Any] = {}
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        pu = getattr(pg, "url", None)
                        if pu:
                            existing[pu.rstrip("/")] = pg

                for url in urls:
                    normalized = url.rstrip("/")
                    if normalized in existing:
                        # Reload existing tab so it has fresh stock state.
                        try:
                            existing[normalized].goto(url, wait_until="commit", timeout=15000)
                            existing[normalized].wait_for_timeout(300)
                            warmed.append(url)
                        except Exception as exc:
                            failed.append(f"{url}: reload failed: {exc}")
                    else:
                        # Open a new isolated context with the Target session.
                        try:
                            ctx = _new_target_context(browser, storage_state)
                            page = ctx.new_page()
                            _goto_target_page(page, url)
                            warmed.append(url)
                        except Exception as exc:
                            failed.append(f"{url}: warm failed: {exc}")
            finally:
                # Unregister service workers via JS before disconnecting so the
                # checkout Lambda's connect_over_cdp doesn't crash on pre-existing
                # SW targets (Playwright CDP assertion in _onAttachedToTarget).
                for ctx in browser.contexts:
                    for pg in ctx.pages:
                        try:
                            pg.evaluate(
                                "async () => { const r = await navigator.serviceWorker.getRegistrations();"
                                " await Promise.all(r.map(x => x.unregister())); }"
                            )
                        except Exception:
                            pass
                kill_cdp_service_workers(cdp_url)
                # Disconnect Playwright; tabs remain open in EC2 Chrome.
                browser.close()

    except Exception as exc:
        return _response(500, {"status": "error", "message": str(exc)})

    return _response(200, {"status": "done", "warmed": len(warmed), "failed": failed, "cdp_probe": cdp_probe})


def _load_secret(secret_arn: str | None) -> str | None:
    if not secret_arn:
        return None
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except Exception:
        return None
    return response.get("SecretString")


def _response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, separators=(",", ":")),
    }
