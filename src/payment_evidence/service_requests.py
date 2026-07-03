from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .amounts import normalize_amount_text

DATE_PATTERN = re.compile(r"^\d{14}$")
HUMAN_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_RESULT_LIMIT = 500
MAX_MAX_PAGES = 25
DEFAULT_MAX_PAGES = 25

_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "merchant_id",
    "merchant",
    "start_date",
    "end_date",
    "amount",
    "last_four",
    "order_id",
    "transaction_id",
    "action_type",
    "condition",
    "transaction_type",
    "result_limit",
    "max_pages",
    "lookback_days",
    "lookahead_days",
    "match",
})


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    normalized: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)


def validate_search_request(form: dict[str, Any]) -> ValidationResult:
    normalized = _normalized_base(form)
    errors = _unknown_field_errors(form)
    errors.extend(_normalize_date_window(normalized))
    errors.extend(_pagination_errors(normalized))
    errors.extend(_window_parameter_errors(normalized))
    if errors:
        return ValidationResult(valid=False, errors=errors)
    return ValidationResult(valid=True, normalized=normalized)


def validate_investigate_request(form: dict[str, Any]) -> ValidationResult:
    result = validate_search_request(form)
    errors = list(result.errors)
    normalized = result.normalized if result.valid else _normalized_base(form)
    if not (_clean(normalized.get("amount")) or _clean(normalized.get("transaction_id")) or _clean(normalized.get("order_id"))):
        errors.append({"field": "request", "code": "concrete_detail_clue_required"})
    if errors:
        return ValidationResult(valid=False, errors=errors)
    return ValidationResult(valid=True, normalized=normalized)


def validation_error_response(result: ValidationResult) -> dict[str, Any]:
    return {"status": "error", "error": "invalid_request", "errors": result.errors}


def _normalized_base(form: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        key: _clean(value)
        for key, value in form.items()
        if key in _ALLOWED_FIELDS and _clean(value) is not None
    }
    normalized.setdefault("result_limit", "100")
    normalized.setdefault("max_pages", str(DEFAULT_MAX_PAGES))
    if "max_pages" in normalized:
        max_pages = _positive_int(normalized["max_pages"])
        normalized["max_pages"] = max_pages if max_pages is not None else normalized["max_pages"]
    if "result_limit" in normalized:
        result_limit = _positive_int(normalized["result_limit"])
        normalized["result_limit"] = str(result_limit) if result_limit is not None else normalized["result_limit"]
    for field_name in ("lookback_days", "lookahead_days"):
        if field_name in normalized:
            number = _non_negative_int(normalized[field_name])
            normalized[field_name] = number if number is not None else normalized[field_name]
    return normalized


def _unknown_field_errors(form: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"field": key, "code": "unknown_field"}
        for key in sorted(str(key) for key in form if str(key) not in _ALLOWED_FIELDS)
    ]


def _normalize_date_window(normalized: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    amount = _clean(normalized.get("amount"))
    for field_name in ("start_date", "end_date"):
        value = normalized.get(field_name)
        if value is None:
            continue
        text = str(value)
        if DATE_PATTERN.fullmatch(text):
            continue
        if HUMAN_DATE_PATTERN.fullmatch(text):
            yyyymmdd = text.replace("-", "")
            suffix = "000000" if field_name == "start_date" else "235959"
            normalized[field_name] = yyyymmdd + suffix
            normalized["date_timezone"] = "UTC"
            continue
        errors.append({"field": field_name, "code": "invalid_date"})
    if amount:
        normalized["amount"] = normalize_amount_text(amount)
        if not _clean(normalized.get("start_date")):
            errors.append({"field": "start_date", "code": "required_for_amount"})
        if not _clean(normalized.get("end_date")):
            errors.append({"field": "end_date", "code": "required_for_amount"})
    return errors


def _pagination_errors(normalized: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for field_name, maximum in (("result_limit", MAX_RESULT_LIMIT), ("max_pages", MAX_MAX_PAGES)):
        value = normalized.get(field_name)
        number = _positive_int(value)
        if number is None:
            errors.append({"field": field_name, "code": "invalid_positive_integer"})
        elif number > maximum:
            errors.append({"field": field_name, "code": "exceeds_maximum"})
    return errors


def _window_parameter_errors(normalized: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for field_name in ("lookback_days", "lookahead_days"):
        if field_name not in normalized:
            continue
        if _non_negative_int(normalized[field_name]) is None:
            errors.append({"field": field_name, "code": "invalid_non_negative_integer"})
    return errors


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _non_negative_int(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
