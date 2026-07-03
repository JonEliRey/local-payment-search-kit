from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .amounts import normalize_amount_text


def rank_candidate_transactions(
    transactions: list[dict[str, Any]],
    *,
    amount: str | None = None,
    last_four: str | None = None,
    order_id: str | None = None,
    transaction_id: str | None = None,
    action_type: str | None = None,
    condition: str | None = None,
    transaction_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Rank gateway transactions using summary-safe search clues.

    Output is intentionally safe for stdout/chat: it excludes raw contact fields,
    customer IDs, postal addresses, full card values, and raw payloads.
    """
    ranked = []
    for txn in transactions:
        candidate = _score_candidate(
            txn,
            amount=amount,
            last_four=last_four,
            order_id=order_id,
            transaction_id=transaction_id,
            action_type=action_type,
            condition=condition,
            transaction_type=transaction_type,
            start_date=start_date,
            end_date=end_date,
        )
        if candidate is not None:
            ranked.append(candidate)

    ranked.sort(key=lambda item: (-int(item["score"]), -int(str(item.get("latest_action_date") or "0") or "0"), str(item.get("transaction_id") or "")))

    for index, candidate in enumerate(ranked, start=1):
        candidate["rank"] = index
        candidate.pop("latest_action_date", None)

    top_score = int(ranked[0]["score"]) if ranked else 0
    ambiguous = len([item for item in ranked if int(item["score"]) == top_score]) > 1 if ranked else False
    return {
        "candidate_summary": {
            "candidate_count": len(ranked),
            "top_score": top_score,
            "ambiguous": ambiguous,
        },
        "candidates": ranked,
    }


def _score_candidate(
    txn: dict[str, Any],
    *,
    amount: str | None,
    last_four: str | None,
    order_id: str | None,
    transaction_id: str | None,
    action_type: str | None,
    condition: str | None,
    transaction_type: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any] | None:
    score = 0
    explanations: list[str] = []
    action_summaries = _action_summaries(txn)
    latest_action_date = _latest_action_date(action_summaries)

    if transaction_id:
        if not _same_text(txn.get("transaction_id"), transaction_id):
            return None
        score = 100
        explanations.append("exact transaction id matched")
    if order_id:
        if not _same_text(txn.get("order_id"), order_id):
            return None
        score += 25
        explanations.append("order id matched")
    if amount:
        if not _has_action_amount(txn, amount):
            return None
        score += 30
        explanations.append("amount matched")
    if last_four:
        if not _matches_last_four(txn, last_four):
            return None
        score += 25
        explanations.append("last four matched")
    if condition:
        if not _same_text(txn.get("condition"), condition):
            return None
        score += 10
        explanations.append("condition matched")
    if transaction_type:
        if not _same_text(txn.get("transaction_type"), transaction_type):
            return None
        score += 10
        explanations.append("transaction type matched")
    if action_type:
        if not _has_action_type(txn, action_type):
            return None
        score += 15
        explanations.append("action type matched")
    if start_date and end_date:
        if not _has_action_inside_window(txn, start_date, end_date):
            return None
        score += 5
        explanations.append("inside date window")

    primary_action = _primary_action_summary(action_summaries)
    score = min(score, 100)
    return {
        "rank": 0,
        "score": score,
        "transaction_id": txn.get("transaction_id"),
        "order_id": txn.get("order_id"),
        "amount": primary_action.get("amount"),
        "date": primary_action.get("date"),
        "last_four": _safe_last_four(txn),
        "condition": txn.get("condition"),
        "transaction_type": txn.get("transaction_type"),
        "currency": txn.get("currency"),
        "cc_type": txn.get("cc_type"),
        "action_summaries": action_summaries,
        "explanations": explanations,
        "latest_action_date": latest_action_date,
    }


def _action_summaries(txn: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for action in txn.get("actions", []) or []:
        output.append(
            {
                "action_type": action.get("action_type"),
                "amount": action.get("amount"),
                "date": action.get("date"),
                "success": action.get("success"),
            }
        )
    return output


def _primary_action_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    dated_actions = [action for action in actions if str(action.get("date", "")).isdigit()]
    if dated_actions:
        return max(dated_actions, key=lambda action: str(action.get("date") or ""))
    return actions[0] if actions else {}


def _safe_last_four(txn: dict[str, Any]) -> str | None:
    for key in ["cc_last_four", "last_four"]:
        value = txn.get(key)
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        if len(digits) == 4:
            return digits
    for key in ["cc_number", "masked_card", "card_number"]:
        value = txn.get(key)
        text = str(value or "")
        if not text or not any(ch in text for ch in "*xX•"):
            continue
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 4:
            return digits[-4:]
    return None


def _latest_action_date(actions: list[dict[str, Any]]) -> str:
    dates = [str(action.get("date")) for action in actions if str(action.get("date", "")).isdigit()]
    return max(dates) if dates else ""


def _has_action_amount(txn: dict[str, Any], amount: str) -> bool:
    expected = _decimal(amount)
    if expected is None:
        return False
    for action in txn.get("actions", []) or []:
        actual = _decimal(str(action.get("amount", "")))
        if actual == expected:
            return True
    return False


def _has_action_type(txn: dict[str, Any], action_type: str) -> bool:
    return any(_same_text(action.get("action_type"), action_type) for action in txn.get("actions", []) or [])


def _has_action_inside_window(txn: dict[str, Any], start_date: str, end_date: str) -> bool:
    if not (_is_timestamp(start_date) and _is_timestamp(end_date)):
        return False
    for action in txn.get("actions", []) or []:
        raw = str(action.get("date", ""))
        if _is_timestamp(raw) and start_date <= raw <= end_date:
            return True
    return False


def _is_timestamp(value: str) -> bool:
    return len(value) == 14 and value.isdigit()


def _matches_last_four(txn: dict[str, Any], last_four: str) -> bool:
    wanted = "".join(ch for ch in str(last_four) if ch.isdigit())[-4:]
    if len(wanted) != 4:
        return False
    for key in ["cc_number", "masked_card", "card_number", "cc_last_four", "last_four"]:
        value = txn.get(key)
        if value is not None and "".join(ch for ch in str(value) if ch.isdigit()).endswith(wanted):
            return True
    return False


def _same_text(left: Any, right: Any) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(normalize_amount_text(str(value))).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
