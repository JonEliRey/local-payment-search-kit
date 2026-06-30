from __future__ import annotations

import json
from typing import Any

from .redaction import redact_transaction


def render_transaction_packet(case_payload: dict[str, Any], *, title: str = "Transaction Information Packet") -> str:
    transactions = [redact_transaction(txn, mode="summary") for txn in case_payload.get("transactions", [])]
    history = case_payload.get("history_summary") or {}
    lines = [
        f"# {title}",
        "",
        "## Case header",
        _json_block({"status": case_payload.get("status"), "merchant": case_payload.get("merchant")}),
        "",
        "## Payment facts",
        _json_block(transactions),
        "",
        "## Action timeline",
        _json_block([{"transaction_id": txn.get("transaction_id"), "actions": txn.get("actions", [])} for txn in transactions]),
        "",
        "## Same-customer history",
        _json_block(history or {"status": "not_attached"}),
        "",
        "## Transaction information checklist",
        "- Gateway transaction lookup result captured",
        "- Summary-safe redaction applied",
        "- Authorization/settlement/refund/void actions reviewed when present",
        "- Same-customer history reviewed when attached",
        "- Non-gateway artifacts must be gathered manually",
        "",
        "## Information limitations",
    ]
    limitations = history.get("information_limitations") if isinstance(history, dict) else None
    if limitations:
        lines.extend(f"- {item}" for item in limitations)
    else:
        lines.append("- Packet is limited to fields present in the local case file.")
    lines.append("- This packet provides transaction information only, not legal advice.")
    lines.append("")
    return "\n".join(lines)


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, sort_keys=True) + "\n```"
