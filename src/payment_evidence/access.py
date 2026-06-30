from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    reason: str


def authorize_merchant(
    identity: Any,
    merchant_alias: str,
    registry: dict[str, Any],
) -> AuthorizationResult:
    """Return an AuthorizationResult for the given identity and merchant.

    The identity object is expected to expose ``role``, ``iso_id``, and
    ``assigned_merchants``.  The registry is a plain dict whose ``"isos"``
    key maps ISO ids to frozensets of merchant aliases.

    Reason codes are opaque safe strings — they never embed internal
    identifiers, org structure, or secrets.
    """
    role = getattr(identity, "role", "")
    if not role:
        return AuthorizationResult(allowed=False, reason="denied: missing_role")

    if role == "ethion_admin":
        return AuthorizationResult(allowed=True, reason="ethion_admin")

    if role in ("ethion_operator", "global_admin"):
        assigned: frozenset[str] = getattr(identity, "assigned_merchants", None) or frozenset()
        if merchant_alias not in assigned:
            return AuthorizationResult(
                allowed=False,
                reason="denied: merchant_not_assigned",
            )
        return AuthorizationResult(
            allowed=True, reason="operator_assigned_access"
        )

    iso_id = getattr(identity, "iso_id", None)

    if role in ("iso_admin", "iso_user"):
        if not iso_id:
            return AuthorizationResult(
                allowed=False, reason="denied: iso_role_requires_iso"
            )
        iso_merchants: frozenset[str] = registry.get("isos", {}).get(iso_id, frozenset())
        if merchant_alias not in iso_merchants:
            return AuthorizationResult(
                allowed=False,
                reason="denied: merchant_not_in_iso",
            )
        return AuthorizationResult(
            allowed=True, reason="iso_access"
        )

    if role in ("merchant_admin", "merchant_user"):
        assigned: frozenset[str] = getattr(identity, "assigned_merchants", None) or frozenset()
        if merchant_alias not in assigned:
            return AuthorizationResult(
                allowed=False,
                reason="denied: merchant_not_assigned",
            )
        return AuthorizationResult(
            allowed=True, reason="merchant_access"
        )

    return AuthorizationResult(
        allowed=False, reason="denied: unknown_role"
    )
