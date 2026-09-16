from __future__ import annotations

import json
from typing import Any


_WORKFLOW_METADATA_KEYS = (
    "ticker",
    "symbol",
    "side",
    "requested_dollars",
    "amount_dollars",
    "dollar_amount",
    "idempotency_key",
    "decision_hash",
)


def extract_mcp_data(response: dict[str, Any]) -> dict[str, Any]:
    """Extract a tool data object from known Robinhood MCP envelope variants."""
    if response.get("isError") or response.get("is_error"):
        texts = [
            item.get("text", "")
            for item in response.get("content") or []
            if isinstance(item, dict)
        ]
        message = " | ".join(text for text in texts if text) or "Robinhood MCP tool failed"
        raise ValueError(message)

    structured = response.get("structuredContent") or response.get("structured_content") or {}
    data = structured.get("data") if isinstance(structured, dict) else None
    if isinstance(data, dict):
        return data

    for item in response.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        parsed_data = parsed.get("data")
        if isinstance(parsed_data, dict):
            return parsed_data
        parsed_structured = parsed.get("structuredContent") or parsed.get("structured_content")
        if isinstance(parsed_structured, dict) and isinstance(parsed_structured.get("data"), dict):
            return parsed_structured["data"]

    keys = ", ".join(sorted(response.keys()))
    raise ValueError(
        "Robinhood MCP response did not contain structured tool data "
        f"(top-level keys: {keys or '<none>'})"
    )


def normalize_order_row(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten observed order envelopes while retaining workflow metadata."""
    nested = row.get("order")
    broker_row = dict(nested) if isinstance(nested, dict) else dict(row)
    if isinstance(nested, dict):
        for key in _WORKFLOW_METADATA_KEYS:
            if key in row and key not in broker_row:
                broker_row[key] = row[key]

    if "id" not in broker_row and broker_row.get("order_id") not in (None, ""):
        broker_row["id"] = broker_row["order_id"]
    if "order_id" not in broker_row and broker_row.get("id") not in (None, ""):
        broker_row["order_id"] = broker_row["id"]
    if "symbol" not in broker_row and broker_row.get("ticker"):
        broker_row["symbol"] = str(broker_row["ticker"]).upper()
    if "ticker" not in broker_row and broker_row.get("symbol"):
        broker_row["ticker"] = str(broker_row["symbol"]).upper()
    if "state" not in broker_row and broker_row.get("status") not in (None, ""):
        broker_row["state"] = broker_row["status"]
    return broker_row


def extract_order_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract canonical order rows from receipts, data objects, or raw snapshots."""
    for key in ("submitted_orders", "orders", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [normalize_order_row(row) for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [normalize_order_row(value)]

    order = payload.get("order")
    if isinstance(order, dict):
        return [normalize_order_row(payload)]

    raw = payload.get("raw_responses") or {}
    response = (raw.get("orders") or {}).get("response") if isinstance(raw, dict) else None
    if isinstance(response, dict):
        try:
            data = extract_mcp_data(response)
        except ValueError:
            return []
        return extract_order_rows(data)

    return []


def broker_order_id(row: dict[str, Any]) -> str:
    normalized = normalize_order_row(row)
    return str(normalized.get("order_id") or normalized.get("id") or "").strip()


def broker_order_amount(row: dict[str, Any]) -> float | None:
    normalized = normalize_order_row(row)
    amount = normalized.get("requested_dollars")
    if amount not in (None, ""):
        return float(amount)
    dollar = normalized.get("dollar_based_amount")
    if isinstance(dollar, dict) and dollar.get("amount") not in (None, ""):
        return float(dollar["amount"])
    for key in ("amount_dollars", "dollar_amount", "amount"):
        if normalized.get(key) not in (None, ""):
            return float(normalized[key])
    return None


def assert_collection_complete(data: dict[str, Any], *, resource: str) -> None:
    """Fail closed when a list response advertises another page we do not consume."""
    pagination = data.get("pagination")
    candidates: list[Any] = [
        data.get("next"),
        data.get("next_url"),
        data.get("next_cursor"),
        data.get("cursor"),
        data.get("page_token"),
    ]
    if isinstance(pagination, dict):
        candidates.extend(
            [
                pagination.get("next"),
                pagination.get("next_url"),
                pagination.get("next_cursor"),
                pagination.get("cursor"),
                pagination.get("page_token"),
            ]
        )
    has_more = data.get("has_more")
    if isinstance(pagination, dict) and has_more is None:
        has_more = pagination.get("has_more")

    if has_more is True or any(value not in (None, "", False, []) for value in candidates):
        raise ValueError(
            f"Robinhood {resource} response indicates additional pagination; "
            "complete retrieval is not proven, so V1 fails closed."
        )
