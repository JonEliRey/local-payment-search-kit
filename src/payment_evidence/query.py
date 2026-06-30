from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .parser import parse_query_response
from .redaction import RedactionMode, redact_transactions

USER_AGENT = "Ethion-MBCard-Investigator/0.1"


@dataclass(frozen=True)
class QueryTrace:
    endpoint: str
    params_without_secret: dict[str, str]
    http_status: int | None
    content_type: str | None
    record_count: int
    redaction_policy: str


def build_query_params(
    *,
    security_key: str,
    transaction_id: str | None = None,
    order_id: str | None = None,
    amount: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    result_limit: str = "10",
    page_number: str = "0",
    condition: str | None = None,
    action_type: str | None = None,
    transaction_type: str | None = None,
    require_lookup_key: bool = True,
) -> dict[str, str]:
    lookup_count = sum(bool(x) for x in [transaction_id, order_id, amount])
    if require_lookup_key and lookup_count != 1:
        raise ValueError("Provide exactly one lookup key: transaction_id, order_id, or amount")
    if not require_lookup_key and lookup_count > 1:
        raise ValueError("Provide at most one lookup key when lookup key is optional")
    if amount and not (start_date and end_date):
        raise ValueError("Amount lookup requires bounded --start-date and --end-date")

    params = {
        "security_key": security_key,
        "result_limit": str(result_limit),
        "page_number": str(page_number),
    }
    if transaction_id:
        params["transaction_id"] = transaction_id
    if order_id:
        params["order_id"] = order_id
    if amount:
        params["amount"] = amount
        params["action_type"] = action_type or "sale"
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if condition:
        params["condition"] = condition
    if action_type and not amount:
        params["action_type"] = action_type
    if transaction_type:
        params["transaction_type"] = transaction_type
    return params


def execute_query(
    base_url: str,
    params: dict[str, str],
    *,
    timeout: int = 20,
    redaction_mode: RedactionMode = "summary",
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/api/query.php"
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/xml",
            "User-Agent": USER_AGENT,
        },
    )
    http_status: int | None = None
    content_type: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            http_status = resp.status
            content_type = resp.headers.get("content-type")
            response_body = resp.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        content_type = exc.headers.get("content-type")
        response_body = exc.read(1024 * 1024)
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": {
                "endpoint": endpoint,
                "params_without_secret": _without_secret(params),
                "http_status": None,
                "content_type": None,
                "record_count": 0,
                "redaction_policy": f"{redaction_mode}-redaction-v1",
            },
            "transactions": [],
        }

    parsed = parse_query_response(response_body)
    transactions = redact_transactions(parsed["transactions"], mode=redaction_mode)
    trace = QueryTrace(
        endpoint=endpoint,
        params_without_secret=_without_secret(params),
        http_status=http_status,
        content_type=content_type,
        record_count=len(transactions),
        redaction_policy=f"{redaction_mode}-redaction-v1",
    )
    return {
        "status": "api_error" if parsed.get("error") else "completed",
        "error": parsed.get("error"),
        "xml_root": parsed.get("xml_root"),
        "trace": trace.__dict__,
        "transactions": transactions,
    }


def _without_secret(params: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in params.items() if k != "security_key"}
