from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SECRET_STORE_PATH = "~/.config/payment-evidence-kit/secrets.json"
VALID_SCOPES = {"gateway", "iso", "merchant", "agent", "case"}


class SecretStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretRef:
    scope: str
    owner: str
    name: str

    @property
    def ref(self) -> str:
        return f"{self.scope}/{self.owner}/{self.name}"


def default_secret_store_path() -> Path:
    if os.environ.get("PAYMENT_SEARCH_SECRET_STORE"):
        return Path(os.environ["PAYMENT_SEARCH_SECRET_STORE"]).expanduser()
    if os.environ.get("PAYMENT_EVIDENCE_SECRET_STORE"):
        return Path(os.environ["PAYMENT_EVIDENCE_SECRET_STORE"]).expanduser()
    try:
        from .local_state import default_secret_store_path as payment_search_default_secret_store_path

        return payment_search_default_secret_store_path()
    except Exception:
        return Path(DEFAULT_SECRET_STORE_PATH).expanduser()


def parse_secret_ref(ref: str) -> SecretRef:
    parts = [part.strip() for part in ref.split("/")]
    if len(parts) != 3 or any(not part for part in parts):
        raise SecretStoreError("local_secret_ref must use '<scope>/<owner>/<name>'")
    scope, owner, name = parts
    _validate_parts(scope, owner, name)
    return SecretRef(scope=scope, owner=owner, name=name)


class LocalSecretStore:
    """Small local secret registry for operators without 1Password/keyring.

    The store is intentionally outside project folders by default and is created
    with owner-only file permissions. Values are never returned by list/set/remove
    metadata methods. This is not a replacement for enterprise secret managers;
    it is the portable local fallback for humans and CLI agents.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else default_secret_store_path()

    def set_secret(self, scope: str, owner: str, name: str, value: str) -> dict[str, Any]:
        _validate_parts(scope, owner, name)
        if not value:
            raise SecretStoreError("secret value cannot be empty")
        data = self._read_data()
        data.setdefault("version", 1)
        data.setdefault("secrets", {})
        data["secrets"].setdefault(scope, {}).setdefault(owner, {})[name] = value
        self._write_data(data)
        return self._metadata(scope, owner, name)

    def get_secret(self, scope: str, owner: str, name: str) -> str:
        _validate_parts(scope, owner, name)
        data = self._read_data()
        try:
            value = data["secrets"][scope][owner][name]
        except KeyError as exc:
            raise SecretStoreError(f"Secret '{scope}/{owner}/{name}' was not found in local secret store") from exc
        if not isinstance(value, str) or not value:
            raise SecretStoreError(f"Secret '{scope}/{owner}/{name}' is empty or invalid")
        return value

    def get_secret_ref(self, ref: str) -> str:
        parsed = parse_secret_ref(ref)
        return self.get_secret(parsed.scope, parsed.owner, parsed.name)

    def remove_secret(self, scope: str, owner: str, name: str) -> dict[str, Any]:
        _validate_parts(scope, owner, name)
        data = self._read_data()
        try:
            del data["secrets"][scope][owner][name]
        except KeyError as exc:
            raise SecretStoreError(f"Secret '{scope}/{owner}/{name}' was not found in local secret store") from exc
        if not data["secrets"].get(scope, {}).get(owner):
            data["secrets"].get(scope, {}).pop(owner, None)
        if not data["secrets"].get(scope):
            data["secrets"].pop(scope, None)
        self._write_data(data)
        return self._metadata(scope, owner, name, removed=True)

    def list_metadata(self) -> list[dict[str, Any]]:
        data = self._read_data()
        output: list[dict[str, Any]] = []
        for scope in sorted(data.get("secrets", {})):
            owners = data["secrets"].get(scope, {})
            for owner in sorted(owners):
                names = owners.get(owner, {})
                for name in sorted(names):
                    output.append(self._metadata(scope, owner, name))
        return output

    def _read_data(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "secrets": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SecretStoreError(f"Secret store is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict):
            raise SecretStoreError(f"Secret store root must be an object: {self.path}")
        data.setdefault("version", 1)
        data.setdefault("secrets", {})
        if not isinstance(data["secrets"], dict):
            raise SecretStoreError(f"Secret store 'secrets' must be an object: {self.path}")
        return data

    def _write_data(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self.path)
        os.chmod(self.path, 0o600)

    def _metadata(self, scope: str, owner: str, name: str, *, removed: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope": scope,
            "owner": owner,
            "name": name,
            "ref": f"{scope}/{owner}/{name}",
            "store": str(self.path),
            "value": "redacted",
        }
        if removed:
            payload["removed"] = True
        return payload


def _validate_parts(scope: str, owner: str, name: str) -> None:
    if scope not in VALID_SCOPES:
        raise SecretStoreError(f"scope must be one of: {', '.join(sorted(VALID_SCOPES))}")
    for label, value in (("owner", owner), ("name", name)):
        if not value or "/" in value:
            raise SecretStoreError(f"{label} must be non-empty and cannot contain '/'")
