from __future__ import annotations

import base64
import gzip
import json
from typing import Any

SECRET_ENCODING_PREFIX = "gzip+base64:"
SECRETS_MANAGER_MAX_STRING_BYTES = 65536


def encode_storage_state_for_secret(storage_state: dict[str, Any]) -> str:
    raw = json.dumps(storage_state, separators=(",", ":"))
    if _utf8_len(raw) <= SECRETS_MANAGER_MAX_STRING_BYTES:
        return raw

    compressed = gzip.compress(raw.encode("utf-8"), compresslevel=9)
    encoded = SECRET_ENCODING_PREFIX + base64.b64encode(compressed).decode("ascii")
    if _utf8_len(encoded) > SECRETS_MANAGER_MAX_STRING_BYTES:
        raise ValueError("Target session is too large for Secrets Manager, even after gzip+base64 encoding")
    return encoded


def decode_storage_state_secret(secret_string: str) -> dict[str, Any]:
    try:
        if secret_string.startswith(SECRET_ENCODING_PREFIX):
            payload = secret_string.removeprefix(SECRET_ENCODING_PREFIX)
            raw = gzip.decompress(base64.b64decode(payload, validate=True)).decode("utf-8")
        else:
            raw = secret_string
        storage_state = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"Target session secret could not be decoded: {exc}") from exc

    if not isinstance(storage_state, dict):
        raise ValueError("Target session secret must decode to a JSON object")
    return storage_state


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))
