from __future__ import annotations

import json
import sys
import types

from poketracker.checkout import target_session


class FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        _ = wait_until
        _ = timeout
        self.goto_calls.append(url)


class FakeContext:
    def __init__(self, page: FakePage, storage_state: dict) -> None:
        self.pages = [page]
        self._storage_state = storage_state

    def new_page(self) -> FakePage:
        return self.pages[0]

    def storage_state(self) -> dict:
        return self._storage_state


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.contexts = [context]
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakePlaywrightManager:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = types.SimpleNamespace(connect_over_cdp=lambda cdp_url: browser)

    def __enter__(self) -> "FakePlaywrightManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = exc_type
        _ = exc
        _ = tb


def test_capture_target_session_from_cdp_writes_state(monkeypatch) -> None:
    page = FakePage()
    storage_state = {"cookies": [], "origins": [{"origin": "https://www.target.com", "localStorage": []}]}
    browser = FakeBrowser(FakeContext(page, storage_state))
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: FakePlaywrightManager(browser)
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    written: dict[str, object] = {}
    monkeypatch.setattr(
        target_session,
        "write_storage_state",
        lambda output_path, state: written.update({"output_path": output_path, "state": state}),
    )

    result = target_session.capture_target_session_from_cdp(
        "test-target-session.json",
        "http://127.0.0.1:9222",
        verify_url="https://www.target.com/p/example",
    )

    assert result == storage_state
    assert browser.closed is True
    assert page.goto_calls == ["https://www.target.com/p/example"]
    assert written == {"output_path": "test-target-session.json", "state": storage_state}


def test_upload_storage_state_secret_uses_encoded_payload_when_needed(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeSecretsClient:
        def put_secret_value(self, SecretId: str, SecretString: str) -> None:
            captured["secret_id"] = SecretId
            captured["secret_string"] = SecretString

    monkeypatch.setattr(target_session.boto3, "client", lambda service_name, region_name: FakeSecretsClient())

    storage_state = {
        "cookies": [{"name": "session", "value": "x" * 70000, "domain": ".target.com", "path": "/"}],
        "origins": [],
    }
    encoding = target_session.upload_storage_state_secret(storage_state, "poketracker-prod-target-session", "us-east-1")

    assert encoding == "gzip+base64 encoded"
    assert captured["secret_id"] == "poketracker-prod-target-session"
    assert captured["secret_string"].startswith("gzip+base64:")
