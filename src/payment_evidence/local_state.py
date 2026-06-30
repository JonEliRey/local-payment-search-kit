from __future__ import annotations

import os
from pathlib import Path

DEFAULT_STATE_DIR = "~/.payment-search"


def default_state_dir() -> Path:
    return Path(os.environ.get("PAYMENT_SEARCH_HOME") or DEFAULT_STATE_DIR).expanduser()


def default_config_path() -> Path:
    return default_state_dir() / "config.json"


def default_secret_store_path() -> Path:
    return Path(os.environ.get("PAYMENT_SEARCH_SECRET_STORE") or default_state_dir() / "secrets.json").expanduser()


def default_artifact_dir() -> Path:
    return default_state_dir() / "artifacts"
