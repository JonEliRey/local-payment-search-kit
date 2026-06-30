from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


CloudflareValidator = Callable[[str], str | None]


@dataclass(frozen=True)
class IdentityExtractionResult:
    allowed: bool
    identity: Any | None
    reason: str


def extract_identity(
    headers: Mapping[str, str],
    registry: Any,
    *,
    mode: str,
    dev_enabled: bool = False,
    cloudflare_validator: CloudflareValidator | None = None,
) -> IdentityExtractionResult:
    """Extract a registry identity from auth context.

    Supported modes:
    - ``dev``: accepts ``X-Payment-Evidence-Dev-User`` only when
      ``dev_enabled`` is true.
    - ``cloudflare``: requires a Cloudflare Access JWT assertion and a
      caller-provided validator. The authenticated-user-email header is
      never trusted by itself.

    The function intentionally returns opaque reason codes only. It does
    not include header names, header values, config paths, or credential
    material in failures.
    """
    normalized = _normalize_headers(headers)

    if mode == "dev":
        if not dev_enabled:
            return _deny("denied: dev_mode_disabled")
        email = normalized.get("x-payment-evidence-dev-user")
        if not email:
            return _deny("denied: missing_identity")
        return _identity_from_email(email, registry)

    if mode == "cloudflare":
        assertion = normalized.get("cf-access-jwt-assertion")
        if not assertion:
            return _deny("denied: missing_cloudflare_assertion")
        if cloudflare_validator is None:
            return _deny("denied: cloudflare_validator_required")
        try:
            email = cloudflare_validator(assertion)
        except Exception:
            return _deny("denied: invalid_cloudflare_assertion")
        if not email:
            return _deny("denied: invalid_cloudflare_assertion")
        return _identity_from_email(email, registry)

    return _deny("denied: unsupported_identity_mode")


def _identity_from_email(email: str, registry: Any) -> IdentityExtractionResult:
    cleaned = email.strip().lower()
    if not _looks_like_email(cleaned):
        return _deny("denied: malformed_identity")
    identity = registry.resolve_identity(cleaned)
    if identity is None:
        return _deny("denied: unknown_identity")
    return IdentityExtractionResult(allowed=True, identity=identity, reason="identity_resolved")


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value).strip() for key, value in headers.items()}


def _looks_like_email(value: str) -> bool:
    if not value or any(ch.isspace() for ch in value):
        return False
    if value.count("@") != 1:
        return False
    local, domain = value.split("@", 1)
    return bool(local and domain and "." in domain)


def _deny(reason: str) -> IdentityExtractionResult:
    return IdentityExtractionResult(allowed=False, identity=None, reason=reason)
