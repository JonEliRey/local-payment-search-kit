from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import MerchantConfig
from .secret_store import LocalSecretStore, SecretStoreError


def _default_op_binary() -> str:
    return os.environ.get("PAYMENT_EVIDENCE_OP_BINARY") or os.environ.get("MBCARD_OP_BINARY") or "op"


class SecretResolutionError(RuntimeError):
    pass


def resolve_security_key(
    merchant: MerchantConfig,
    *,
    op_binary: str | None = None,
    secret_store_path: str | Path | None = None,
) -> str:
    op_binary = op_binary or _default_op_binary()
    if merchant.env_var:
        value = os.environ.get(merchant.env_var)
        if value:
            return value
    if merchant.local_secret_ref:
        try:
            return LocalSecretStore(secret_store_path).get_secret_ref(merchant.local_secret_ref)
        except SecretStoreError as exc:
            raise SecretResolutionError(str(exc)) from exc
    if merchant.op_item and merchant.op_field:
        return read_op_field(merchant.op_item, merchant.op_field, merchant.op_vault, op_binary=op_binary)
    raise SecretResolutionError(
        f"Merchant '{merchant.alias}' has no env_var value, local_secret_ref, or 1Password item/field reference"
    )


def read_op_field(item: str, field: str, vault: str, *, op_binary: str | None = None) -> str:
    op_binary = op_binary or _default_op_binary()
    selectors = [f"id={field}", f"label={field}"] if _looks_like_field_id(field) else [f"label={field}", f"id={field}"]
    errors: list[str] = []
    for selector in selectors:
        cmd = [
            op_binary,
            "item",
            "get",
            item,
            "--vault",
            vault,
            "--fields",
            selector,
            "--reveal",
        ]
        completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
        errors.append(_sanitize_error(completed.stderr or completed.stdout))
    raise SecretResolutionError(
        f"Could not read 1Password field '{field}' on item '{item}': " + " | ".join(errors)
    )


def _looks_like_field_id(field: str) -> bool:
    return len(field) >= 20 and field.islower() and field.isalnum()


def _sanitize_error(message: str) -> str:
    return " ".join(message.strip().split())[:500]
