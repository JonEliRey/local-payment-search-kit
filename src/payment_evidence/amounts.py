from __future__ import annotations

from decimal import Decimal, InvalidOperation


def normalize_amount_text(value: str) -> str:
    """Return gateway-friendly amount text for common human-entered money values."""
    text = str(value).strip()
    if not text:
        return text
    candidate = text.replace(",", "")
    if candidate.startswith("$"):
        candidate = candidate[1:].strip()
    try:
        Decimal(candidate).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return text
    return candidate
