from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

MATCH_KEY_ALIASES = {
    "customer_id": ("customerid", "customer_id", "customer_vault_id"),
    "email": ("email",),
    "masked_card": ("cc_number", "masked_card"),
    "billing_zip": ("postal_code", "billing_zip", "billing_postal_code"),
    "billing_address": ("address_1", "address_2", "city", "state", "postal_code"),
    "name": ("first_name", "last_name"),
}

STRONG_KEYS = {"customer_id", "masked_card", "email"}
MEDIUM_COMBOS = {"billing_zip", "billing_address"}


def summarize_customer_history(
    confirmed_transaction: dict[str, Any],
    candidate_transactions: list[dict[str, Any]],
    *,
    requested_match_keys: list[str] | None = None,
) -> dict[str, Any]:
    requested = requested_match_keys or ["customer_id", "masked_card", "email", "billing_zip"]
    available = [key for key in requested if _match_value(confirmed_transaction, key)]
    used = [key for key in available if key != "name"]
    matched = [txn for txn in candidate_transactions if _is_prior_match(confirmed_transaction, txn, used)]

    prior_settled = sum(1 for txn in matched if _has_action(txn, {"settle", "settlement", "capture"}, success="1"))
    failed = sum(1 for txn in matched if _is_failed(txn))
    refunded_or_voided = sum(1 for txn in matched if _has_action(txn, {"refund", "void", "credit"}))
    total = sum((_transaction_amount(txn) for txn in matched), Decimal("0.00"))

    return {
        "confirmed_transaction_id": confirmed_transaction.get("transaction_id"),
        "confirmed_order_id": confirmed_transaction.get("order_id"),
        "match_keys_available": available,
        "match_keys_used": used,
        "match_confidence": _confidence(used),
        "prior_transaction_count": len(matched),
        "prior_settled_count": prior_settled,
        "failed_count": failed,
        "refunded_or_voided_count": refunded_or_voided,
        "total_prior_amount": f"{total:.2f}",
        "currency": _first_present([txn.get("currency") for txn in [confirmed_transaction, *matched]]),
        "prior_transactions": [_safe_prior_summary(txn) for txn in matched],
        "pattern_summary": _pattern_summary(len(matched), prior_settled, failed, refunded_or_voided),
        "information_limitations": _limitations(available, used),
    }


def _is_prior_match(confirmed: dict[str, Any], candidate: dict[str, Any], keys: list[str]) -> bool:
    if not keys:
        return False
    if candidate.get("transaction_id") == confirmed.get("transaction_id"):
        return False
    matched_keys = [key for key in keys if _values_equal(_match_value(confirmed, key), _match_value(candidate, key))]
    if "customer_id" in matched_keys:
        return True
    if "masked_card" in matched_keys and "billing_zip" in matched_keys:
        return True
    if "email" in matched_keys:
        return True
    return len(set(matched_keys) & STRONG_KEYS) >= 1 and len(matched_keys) >= 2


def _match_value(txn: dict[str, Any], key: str) -> str | None:
    aliases = MATCH_KEY_ALIASES.get(key, (key,))
    values = []
    for alias in aliases:
        raw = txn.get(alias)
        if raw not in (None, "", [], {}):
            values.append(str(raw).strip().lower())
    if not values:
        return None
    if key == "masked_card":
        return _normalize_masked_card(values[0])
    return "|".join(values)


def _normalize_masked_card(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum() or ch in {"x", "*"})


def _values_equal(left: str | None, right: str | None) -> bool:
    return bool(left and right and left == right)


def _has_action(txn: dict[str, Any], action_types: set[str], *, success: str | None = None) -> bool:
    for action in txn.get("actions", []):
        action_type = str(action.get("action_type", "")).lower()
        if action_type not in action_types:
            continue
        if success is not None and str(action.get("success", "")) != success:
            continue
        return True
    return False


def _is_failed(txn: dict[str, Any]) -> bool:
    condition = str(txn.get("condition", "")).lower()
    if condition in {"failed", "declined", "error"}:
        return True
    return any(str(action.get("success", "")) == "0" for action in txn.get("actions", []))


def _transaction_amount(txn: dict[str, Any]) -> Decimal:
    for action in txn.get("actions", []):
        amount = action.get("amount")
        if amount in (None, ""):
            continue
        try:
            return Decimal(str(amount))
        except InvalidOperation:
            continue
    return Decimal("0.00")


def _safe_prior_summary(txn: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": txn.get("transaction_id"),
        "order_id": txn.get("order_id"),
        "condition": txn.get("condition"),
        "transaction_type": txn.get("transaction_type"),
        "currency": txn.get("currency"),
        "action_count": len(txn.get("actions", [])),
        "actions": [
            {
                "action_type": action.get("action_type"),
                "amount": action.get("amount"),
                "date": action.get("date"),
                "success": action.get("success"),
                "response_code": action.get("response_code"),
                "response_text": action.get("response_text"),
            }
            for action in txn.get("actions", [])
        ],
    }


def _confidence(keys: list[str]) -> str:
    keyset = set(keys)
    if "customer_id" in keyset or {"masked_card", "billing_zip"}.issubset(keyset):
        return "high"
    if "email" in keyset:
        return "medium"
    if keyset & MEDIUM_COMBOS:
        return "low"
    return "insufficient"


def _pattern_summary(total: int, settled: int, failed: int, refunded_or_voided: int) -> str:
    if total == 0:
        return "No prior same-customer transactions matched the selected keys in the queried window."
    return f"Matched {total} prior transaction(s): {settled} settled, {failed} failed/declined, {refunded_or_voided} refunded/voided."


def _limitations(available: list[str], used: list[str]) -> list[str]:
    limitations = []
    if not used:
        limitations.append("No stable same-customer match keys were available; history could not be established.")
    if "name" in available and len(used) == 1:
        limitations.append("Name alone is weak and is never sufficient by itself.")
    if "customer_id" not in used:
        limitations.append("Gateway customer ID was not available; matching may be less authoritative.")
    limitations.append("History reflects gateway records returned inside the selected lookback window only.")
    return limitations


def _first_present(values: list[Any]) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None
