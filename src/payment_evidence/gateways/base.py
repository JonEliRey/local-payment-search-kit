from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class GatewayCapability:
    name: str
    supported: bool
    notes: str = ""


@dataclass(frozen=True)
class GatewayAdapterInfo:
    name: str
    display_name: str
    capabilities: list[GatewayCapability] = field(default_factory=list)


class GatewayAdapter(Protocol):
    info: GatewayAdapterInfo

    def get_transaction(self, transaction_id: str) -> dict[str, Any]: ...

    def search_transactions(self, **filters: Any) -> list[dict[str, Any]]: ...
