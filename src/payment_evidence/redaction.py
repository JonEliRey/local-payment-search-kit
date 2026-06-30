from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

RedactionMode = Literal["summary", "internal"]

SUMMARY_SENSITIVE_FIELDS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "cell_phone",
    "fax",
    "address_1",
    "address_2",
    "city",
    "state",
    "postal_code",
    "country",
    "company",
    "customerid",
    "customertaxid",
    "website",
    "shipping_first_name",
    "shipping_last_name",
    "shipping_address_1",
    "shipping_address_2",
    "shipping_company",
    "shipping_city",
    "shipping_state",
    "shipping_postal_code",
    "shipping_country",
    "shipping_email",
    "shipping_phone",
    "tracking_number",
    "cc_number",
    "cc_hash",
    "cc_exp",
    "cc_start_date",
    "cc_issue_number",
    "cc_bin",
    "network_token",
    "network_token_expiration",
    "token_or_card_number",
    "check_account",
    "check_hash",
    "check_aba",
    "check_name",
    "drivers_license_number",
    "drivers_license_state",
    "drivers_license_dob",
    "social_security_number",
    "signature_image",
}

# Internal mode is for authorized local detail files after the user confirms the
# candidate transaction. It preserves cardholder/billing context and safe masked
# card data, but still refuses raw account identifiers and high-risk artifacts.
INTERNAL_ALWAYS_REDACT_FIELDS = {
    "cc_hash",
    "cc_bin",
    "network_token",
    "network_token_expiration",
    "token_or_card_number",
    "check_account",
    "check_hash",
    "check_aba",
    "drivers_license_number",
    "drivers_license_state",
    "drivers_license_dob",
    "social_security_number",
    "signature_image",
}

CONDITIONALLY_SAFE_CARD_FIELDS = {"cc_number"}
DEFAULT_DROP_EMPTY = True


def redact_transactions(
    transactions: list[dict[str, Any]],
    *,
    mode: RedactionMode = "summary",
    drop_empty: bool = DEFAULT_DROP_EMPTY,
) -> list[dict[str, Any]]:
    return [redact_transaction(txn, mode=mode, drop_empty=drop_empty) for txn in transactions]


def redact_transaction(
    transaction: dict[str, Any],
    *,
    mode: RedactionMode = "summary",
    drop_empty: bool = DEFAULT_DROP_EMPTY,
) -> dict[str, Any]:
    if mode not in ("summary", "internal"):
        raise ValueError("mode must be 'summary' or 'internal'")
    redacted = deepcopy(transaction)
    return _redact_mapping(redacted, mode=mode, drop_empty=drop_empty)


def _redact_mapping(value: Any, *, mode: RedactionMode, drop_empty: bool) -> Any:
    if isinstance(value, list):
        return [_redact_mapping(item, mode=mode, drop_empty=drop_empty) for item in value]
    if not isinstance(value, dict):
        return value

    output: dict[str, Any] = {}
    for key, item in value.items():
        if _should_redact(key, item, mode):
            if item not in (None, "", [], {}):
                output[f"{key}_redacted"] = True
            continue
        if drop_empty and item in (None, "", [], {}):
            continue
        output[key] = _redact_mapping(item, mode=mode, drop_empty=drop_empty)
    return output


def _should_redact(key: str, item: Any, mode: RedactionMode) -> bool:
    if mode == "summary":
        return key in SUMMARY_SENSITIVE_FIELDS
    if key in INTERNAL_ALWAYS_REDACT_FIELDS:
        return True
    if key in CONDITIONALLY_SAFE_CARD_FIELDS:
        return not _is_masked_card_value(str(item or ""))
    return False


def _is_masked_card_value(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    if "x" in lowered or "*" in lowered:
        return True
    digits = "".join(ch for ch in value if ch.isdigit())
    return 0 < len(digits) <= 4
