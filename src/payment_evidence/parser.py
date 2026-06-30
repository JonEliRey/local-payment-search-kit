from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any


def parse_query_response(body: bytes | str) -> dict[str, Any]:
    if isinstance(body, str):
        body = body.encode("utf-8")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return {
            "xml_root": None,
            "error": f"XML parse error: {exc}",
            "transactions": [],
        }

    error_el = root.find(".//error_response")
    transactions = [_parse_transaction(el) for el in root.findall(".//transaction")]
    return {
        "xml_root": root.tag,
        "error": (error_el.text or "").strip() if error_el is not None else None,
        "transactions": transactions,
    }


def _parse_transaction(txn_el: ET.Element) -> dict[str, Any]:
    transaction: dict[str, Any] = {"actions": []}
    repeated: dict[str, list[Any]] = {}

    for child in list(txn_el):
        tag = _clean_tag(child.tag)
        if tag == "action":
            transaction["actions"].append(_parse_flat_element(child))
            continue
        if list(child):
            value: Any = _parse_flat_element(child)
        else:
            value = (child.text or "").strip()
        if tag in transaction:
            repeated.setdefault(tag, [transaction.pop(tag)]).append(value)
        elif tag in repeated:
            repeated[tag].append(value)
        else:
            transaction[tag] = value

    for tag, values in repeated.items():
        transaction[tag] = values
    return transaction


def _parse_flat_element(parent: ET.Element) -> dict[str, str]:
    output: dict[str, str] = {}
    repeated: dict[str, list[str]] = {}
    for child in list(parent):
        tag = _clean_tag(child.tag)
        value = (child.text or "").strip()
        if tag in output:
            repeated.setdefault(tag, [output.pop(tag)]).append(value)
        elif tag in repeated:
            repeated[tag].append(value)
        else:
            output[tag] = value
    for tag, values in repeated.items():
        output[tag] = values  # type: ignore[assignment]
    return output


def _clean_tag(tag: str) -> str:
    return tag.strip() if isinstance(tag, str) else str(tag)
