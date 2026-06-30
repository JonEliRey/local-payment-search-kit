from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ArtifactPathError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    owner_user_id: str
    tenant_id: str
    merchant_alias: str
    expires_at: datetime
    local_path: Path
    original_name: str

    def public_ref(self) -> str:
        return f"artifact:{self.artifact_id}"


@dataclass(frozen=True)
class ArtifactAccessResult:
    status: str
    path: Path | None = None
    record: ArtifactRecord | None = None


class ArtifactStore:
    def __init__(self, root: str | Path, *, ttl_seconds: int) -> None:
        self.root = Path(root).expanduser().resolve()
        self.ttl_seconds = ttl_seconds
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def put_existing_file(
        self,
        source: str | Path,
        *,
        artifact_type: str,
        owner_user_id: str,
        tenant_id: str,
        merchant_alias: str,
        original_name: str | None = None,
    ) -> ArtifactRecord:
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists() or not source_path.is_file():
            raise ArtifactPathError("source_not_file")
        safe_original_name = self._safe_original_name(original_name or source_path.name)
        artifact_id = secrets.token_hex(32)
        suffix = Path(safe_original_name).suffix
        target = (self.root / f"{artifact_id}{suffix}").resolve()
        if not target.is_relative_to(self.root):
            raise ArtifactPathError("artifact_path_escape")
        shutil.copyfile(source_path, target)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=str(artifact_type),
            owner_user_id=str(owner_user_id),
            tenant_id=str(tenant_id),
            merchant_alias=str(merchant_alias),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
            local_path=target,
            original_name=safe_original_name,
        )
        index = self._load_index()
        index[artifact_id] = self._record_to_json(record)
        self._write_index(index)
        return record

    def resolve_for_access(
        self,
        artifact_id: str,
        *,
        owner_user_id: str,
        tenant_id: str,
        merchant_alias: str,
    ) -> ArtifactAccessResult:
        record = self._get_record(artifact_id)
        if record is None:
            return ArtifactAccessResult("not_found")
        if (
            record.owner_user_id != owner_user_id
            or record.tenant_id != tenant_id
            or record.merchant_alias != merchant_alias
        ):
            return ArtifactAccessResult("denied")
        if datetime.now(timezone.utc) >= record.expires_at:
            return ArtifactAccessResult("expired", record=record)
        if not record.local_path.exists() or not record.local_path.is_file() or not record.local_path.resolve().is_relative_to(self.root):
            return ArtifactAccessResult("not_found", record=record)
        return ArtifactAccessResult("ok", path=record.local_path, record=record)

    def metadata(self, artifact_id: str) -> dict[str, Any]:
        record = self._get_record(artifact_id)
        if record is None:
            return {"status": "not_found"}
        return {
            "artifact_id": record.artifact_id,
            "artifact_type": record.artifact_type,
            "owner_user_id": record.owner_user_id,
            "tenant_id": record.tenant_id,
            "merchant_alias": record.merchant_alias,
            "expires_at": record.expires_at.isoformat(),
            "storage_key": record.local_path.name,
            "original_name": record.original_name,
        }

    def list_for_access(self, *, owner_user_id: str, tenant_id: str, merchant_aliases: set[str]) -> list[ArtifactRecord]:
        records: list[ArtifactRecord] = []
        for artifact_id, data in self._load_index().items():
            record = self._record_from_json(artifact_id, data)
            if datetime.now(timezone.utc) >= record.expires_at:
                continue
            if record.owner_user_id != owner_user_id or record.tenant_id != tenant_id:
                continue
            if record.merchant_alias not in merchant_aliases:
                continue
            if not record.local_path.exists() or not record.local_path.is_file() or not record.local_path.resolve().is_relative_to(self.root):
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.expires_at, reverse=True)

    def _safe_original_name(self, original_name: str) -> str:
        candidate = Path(original_name)
        if candidate.is_absolute() or ".." in candidate.parts or len(candidate.parts) != 1:
            raise ArtifactPathError("invalid_original_name")
        if not candidate.name or candidate.name in {".", ".."}:
            raise ArtifactPathError("invalid_original_name")
        return candidate.name

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _write_index(self, index: dict[str, dict[str, Any]]) -> None:
        self.index_path.write_text(json.dumps(index, sort_keys=True, indent=2), encoding="utf-8")

    def _get_record(self, artifact_id: str) -> ArtifactRecord | None:
        data = self._load_index().get(artifact_id)
        if not data:
            return None
        return self._record_from_json(artifact_id, data)

    def _record_from_json(self, artifact_id: str, data: dict[str, Any]) -> ArtifactRecord:
        local_path = (self.root / data["storage_key"]).resolve()
        if not local_path.is_relative_to(self.root):
            raise ArtifactPathError("artifact_path_escape")
        return ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=data["artifact_type"],
            owner_user_id=data["owner_user_id"],
            tenant_id=data["tenant_id"],
            merchant_alias=data["merchant_alias"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            local_path=local_path,
            original_name=data["original_name"],
        )

    def _record_to_json(self, record: ArtifactRecord) -> dict[str, Any]:
        return {
            "artifact_type": record.artifact_type,
            "owner_user_id": record.owner_user_id,
            "tenant_id": record.tenant_id,
            "merchant_alias": record.merchant_alias,
            "expires_at": record.expires_at.isoformat(),
            "storage_key": record.local_path.name,
            "original_name": record.original_name,
        }
