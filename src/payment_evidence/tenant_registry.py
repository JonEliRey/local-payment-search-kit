from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# -- error types --

class RegistryValidationError(ValueError):
    """Raised when the tenant registry config fails validation."""


# -- identity produced by the registry --

@dataclass(frozen=True)
class RegistryIdentity:
    user_id: str
    role: str
    tenant_id: str | None = None
    iso_id: str | None = None
    assigned_merchants: frozenset[str] = field(default_factory=frozenset)


# -- supported roles --

VALID_ROLES: frozenset[str] = frozenset({
    "ethion_admin",
    "ethion_operator",
    "global_admin",
    "iso_admin",
    "iso_user",
    "merchant_admin",
    "merchant_user",
})


class TenantRegistry:
    """Load and validate a synthetic tenant registry config for PoC RBAC.

    The registry is read-only at runtime.  It provides methods to resolve
    users into stable ``RegistryIdentity`` objects and to query the ISO /
    merchant hierarchy for the ``authorize_merchant`` function in
    ``payment_evidence.access``.
    """

    def __init__(self, config_path: str | Path) -> None:
        path = Path(config_path).expanduser()
        raw = json.loads(path.read_text())
        self._path = path
        self._data = self._validate(raw)

    # -- public read API --

    def tenant_ids(self) -> frozenset[str]:
        """Return all tenant aliases."""
        return frozenset(self._data["tenants"])

    def iso_ids(self) -> frozenset[str]:
        """Return all ISO aliases."""
        return frozenset(self._data["isos"])

    def merchant_ids(self) -> frozenset[str]:
        """Return all merchant aliases."""
        return frozenset(self._data["merchants"])

    def resolve_identity(self, email: str) -> RegistryIdentity | None:
        """Return a ``RegistryIdentity`` for *email*, or ``None``."""
        user_entry = self._data["users"].get(email)
        if user_entry is None:
            return None
        return RegistryIdentity(
            user_id=email,
            role=user_entry["role"],
            tenant_id=user_entry.get("tenant"),
            iso_id=user_entry.get("iso"),
            assigned_merchants=frozenset(user_entry.get("assigned_merchants", ())),
        )

    def iso_merchants(self, iso_id: str) -> frozenset[str]:
        """Return all merchant aliases under *iso_id*."""
        iso_entry = self._data.get("isos", {}).get(iso_id)
        if iso_entry is None:
            return frozenset()
        return frozenset(iso_entry.get("merchants", ()))

    def merchant_iso(self, merchant_alias: str) -> str | None:
        """Return the ISO that owns *merchant_alias*, or ``None``."""
        merchant_entry = self._data.get("merchants", {}).get(merchant_alias)
        if merchant_entry is None:
            return None
        return merchant_entry.get("iso")

    def tenant_display_name(self, tenant_id: str | None) -> str | None:
        if not tenant_id:
            return None
        tenant_entry = self._data.get("tenants", {}).get(tenant_id)
        if not tenant_entry:
            return None
        return str(tenant_entry.get("display_name") or tenant_id)

    def merchant_display_name(self, merchant_alias: str) -> str:
        merchant_entry = self._data.get("merchants", {}).get(merchant_alias)
        if not merchant_entry:
            return merchant_alias
        return str(merchant_entry.get("display_name") or merchant_alias)

    def as_auth_registry(self) -> dict[str, Any]:
        """Return a registry dict compatible with ``authorize_merchant``."""
        return {
            "isos": {
                iso: self.iso_merchants(iso)
                for iso in self._data.get("isos", {})
            }
        }

    # -- internal validation --

    def _validate(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise RegistryValidationError("registry config must be a dict with version=1")

        tenants: dict[str, Any] = raw.get("tenants", {})
        isos: dict[str, Any] = raw.get("isos", {})
        merchants: dict[str, Any] = raw.get("merchants", {})
        users: dict[str, Any] = raw.get("users", {})

        # -- alias uniqueness --
        self._check_no_duplicates("tenant", tenants)
        self._check_no_duplicates("iso", isos)
        self._check_no_duplicates("merchant", merchants)

        # -- referential integrity --
        all_iso_ids = frozenset(isos)
        all_merchant_ids = frozenset(merchants)

        for tenant_alias, tenant in tenants.items():
            for iso_ref in tenant.get("isos", ()):
                if iso_ref not in all_iso_ids:
                    raise RegistryValidationError(
                        f"tenant '{tenant_alias}' references unknown iso '{iso_ref}'"
                    )

        for merchant_alias, merchant in merchants.items():
            iso_ref = merchant.get("iso")
            if iso_ref is None or iso_ref not in all_iso_ids:
                raise RegistryValidationError(
                    f"merchant '{merchant_alias}' references unknown iso '{iso_ref}'"
                )

        for iso_alias, iso in isos.items():
            for merchant_ref in iso.get("merchants", ()):
                if merchant_ref not in all_merchant_ids:
                    raise RegistryValidationError(
                        f"iso '{iso_alias}' references unknown merchant '{merchant_ref}'"
                    )
                merchant_iso = merchants[merchant_ref].get("iso")
                if merchant_iso != iso_alias:
                    raise RegistryValidationError(
                        f"iso '{iso_alias}' references merchant '{merchant_ref}' owned by iso '{merchant_iso}'"
                    )

        # -- user validation --
        for email, user in users.items():
            role = user.get("role", "")
            if role not in VALID_ROLES:
                raise RegistryValidationError(
                    f"user '{email}' has invalid role '{role}'"
                )

            tenant_ref = user.get("tenant")
            if tenant_ref and tenant_ref not in tenants:
                raise RegistryValidationError(
                    f"user '{email}' references unknown tenant '{tenant_ref}'"
                )

            # iso-scoped roles must have an iso set
            if role in ("iso_admin", "iso_user"):
                iso_ref = user.get("iso")
                if not iso_ref or iso_ref not in all_iso_ids:
                    raise RegistryValidationError(
                        f"user '{email}' ({role}) requires a valid iso"
                    )
                if user.get("assigned_merchants"):
                    raise RegistryValidationError(
                        f"user '{email}' ({role}) must not set assigned_merchants"
                    )

            # merchant-scoped/operator MVP1 roles validate assigned_merchants
            if role in ("merchant_admin", "merchant_user", "ethion_operator", "global_admin"):
                assigned = user.get("assigned_merchants", ())
                if not assigned:
                    raise RegistryValidationError(
                        f"user '{email}' ({role}) requires at least one assigned_merchant"
                    )
                for m_ref in assigned:
                    if m_ref not in all_merchant_ids:
                        raise RegistryValidationError(
                            f"user '{email}' references unknown merchant '{m_ref}'"
                        )

        return {
            "tenants": tenants,
            "isos": isos,
            "merchants": merchants,
            "users": users,
        }

    @staticmethod
    def _check_no_duplicates(kind: str, collection: dict[str, Any]) -> None:
        if len(collection) != len(set(collection)):
            raise RegistryValidationError(f"duplicate {kind} aliases are not allowed")
