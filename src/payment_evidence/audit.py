from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_TOP_LEVEL_FIELDS = {
    "request_id",
    "timestamp",
    "action",
    "status",
    "reason",
    "merchant_alias",
    "identity",
    "error_class",
}
SAFE_IDENTITY_FIELDS = {"user_id", "role", "tenant_id", "iso_id"}


class AuditAppendError(RuntimeError):
    pass


def append_audit_event(path: str | Path, event: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_audit_event(event)
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sanitized, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception as exc:  # pragma: no cover - exact OS errors vary
        raise AuditAppendError("audit_unavailable") from exc
    return sanitized


def read_audit_events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def sanitize_audit_event(event: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {
        "request_id": str(event.get("request_id") or uuid.uuid4()),
        "timestamp": str(event.get("timestamp") or datetime.now(timezone.utc).isoformat()),
    }
    for key in ("action", "status", "reason", "merchant_alias", "error_class"):
        value = event.get(key)
        if value is not None:
            sanitized[key] = _safe_scalar(value)
    identity = event.get("identity")
    if isinstance(identity, dict):
        safe_identity = {key: _safe_scalar(identity[key]) for key in SAFE_IDENTITY_FIELDS if identity.get(key) is not None}
        if safe_identity:
            sanitized["identity"] = safe_identity
    return {key: sanitized[key] for key in SAFE_TOP_LEVEL_FIELDS if key in sanitized}


def _safe_scalar(value: Any) -> str:
    return str(value)[:200]
