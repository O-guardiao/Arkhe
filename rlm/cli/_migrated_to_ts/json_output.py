from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _json_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_cli_json_envelope(command: str, payload: dict[str, Any], *, severity: str) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "command": command,
        "generated_at": _json_now(),
        "severity": severity,
        "payload": payload,
    }
    return {**envelope, **payload}