from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://mbcard.transactiongateway.com"
DEFAULT_CONFIG_PATH = "config/gateways.json"


@dataclass(frozen=True)
class MerchantConfig:
    alias: str
    display_name: str
    gateway: str
    base_url: str
    op_item: str | None
    op_vault: str
    op_field: str | None
    env_var: str | None
    local_secret_ref: str | None = None


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path:
        return Path(config_path)
    if os.environ.get("PAYMENT_EVIDENCE_CONFIG"):
        return Path(os.environ["PAYMENT_EVIDENCE_CONFIG"])
    if os.environ.get("MBCARD_CONFIG"):
        return Path(os.environ["MBCARD_CONFIG"])
    return Path(DEFAULT_CONFIG_PATH)


def load_merchant_config(config_path: str | Path | None, alias: str) -> MerchantConfig:
    path = _resolve_config_path(config_path)
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text())
    merchants = data.get("merchants", {})
    if alias not in merchants:
        available = ", ".join(sorted(merchants)) or "none"
        raise KeyError(f"Unknown merchant alias '{alias}'. Available aliases: {available}")

    entry = merchants[alias]
    return MerchantConfig(
        alias=alias,
        display_name=entry.get("display_name") or alias,
        gateway=entry.get("gateway") or data.get("gateway") or "nmi",
        base_url=entry.get("base_url") or data.get("base_url") or DEFAULT_BASE_URL,
        op_item=entry.get("op_item") or data.get("op_item"),
        op_vault=entry.get("op_vault") or data.get("op_vault") or "Operations",
        op_field=entry.get("field") or entry.get("op_field"),
        env_var=entry.get("env_var"),
        local_secret_ref=entry.get("local_secret_ref"),
    )


def resolve_default_merchant_alias(config_path: str | Path | None, alias: str | None) -> str | None:
    if alias:
        return alias
    if os.environ.get("PAYMENT_EVIDENCE_MERCHANT"):
        return os.environ["PAYMENT_EVIDENCE_MERCHANT"]
    if os.environ.get("MBCARD_MERCHANT"):
        return os.environ["MBCARD_MERCHANT"]
    path = _resolve_config_path(config_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data.get("default_merchant"):
        return data["default_merchant"]
    merchants = data.get("merchants", {})
    if len(merchants) == 1:
        return next(iter(merchants))
    return None


def load_configured_aliases(config_path: str | Path | None) -> list[str]:
    path = _resolve_config_path(config_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return sorted(data.get("merchants", {}).keys())
