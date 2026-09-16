import pytest

from finance.broker.robinhood_normalize import (
    assert_collection_complete,
    broker_order_amount,
    broker_order_id,
    extract_mcp_data,
    extract_order_rows,
    normalize_order_row,
)


def test_extract_mcp_data_accepts_structured_content_snake_case() -> None:
    response = {
        "is_error": False,
        "structured_content": {"data": {"orders": [{"id": "o1"}]}},
    }
    assert extract_mcp_data(response) == {"orders": [{"id": "o1"}]}


def test_extract_mcp_data_accepts_structured_content_camel_case() -> None:
    response = {
        "isError": False,
        "structuredContent": {"data": {"positions": []}},
    }
    assert extract_mcp_data(response) == {"positions": []}


def test_normalize_nested_placement_order_preserves_workflow_metadata() -> None:
    row = {
        "ticker": "AAA",
        "requested_dollars": 1.0,
        "idempotency_key": "k1",
        "decision_hash": "d1",
        "order": {
            "id": "o1",
            "symbol": "AAA",
            "side": "buy",
            "state": "filled",
            "dollar_based_amount": {"amount": "1.00"},
        },
    }
    normalized = normalize_order_row(row)
    assert normalized["id"] == "o1"
    assert normalized["order_id"] == "o1"
    assert normalized["ticker"] == "AAA"
    assert normalized["requested_dollars"] == 1.0
    assert normalized["idempotency_key"] == "k1"
    assert broker_order_id(row) == "o1"
    assert broker_order_amount(row) == 1.0


def test_extract_order_rows_from_raw_snapshot_handles_snake_case_envelope() -> None:
    payload = {
        "raw_responses": {
            "orders": {
                "response": {
                    "structured_content": {
                        "data": {
                            "orders": [
                                {
                                    "id": "o1",
                                    "symbol": "AAA",
                                    "state": "queued",
                                    "dollar_based_amount": {"amount": "1.00"},
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    rows = extract_order_rows(payload)
    assert len(rows) == 1
    assert rows[0]["order_id"] == "o1"
    assert rows[0]["ticker"] == "AAA"


def test_pagination_marker_fails_closed() -> None:
    with pytest.raises(ValueError, match="additional pagination"):
        assert_collection_complete(
            {"orders": [{"id": "o1"}], "next_cursor": "cursor-2"},
            resource="orders",
        )


def test_explicit_has_more_fails_closed() -> None:
    with pytest.raises(ValueError, match="additional pagination"):
        assert_collection_complete(
            {"positions": [], "pagination": {"has_more": True}},
            resource="positions",
        )


def test_complete_collection_passes() -> None:
    assert_collection_complete({"orders": [{"id": "o1"}]}, resource="orders")
