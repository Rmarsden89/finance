from finance.shadow.post_fill import reconcile_post_fill_portfolio


def _state(quantity_a: float, quantity_b: float, cash: float, bp: float, *, snake_case: bool = False) -> dict:
    positions = []
    valuations = []
    for ticker, quantity in (("AAA", quantity_a), ("BBB", quantity_b)):
        if quantity > 0:
            positions.append({"symbol": ticker, "quantity": str(quantity)})
            valuations.append(
                {
                    "symbol": ticker,
                    "derived_market_value": str(quantity * 100),
                }
            )
    key = "structured_content" if snake_case else "structuredContent"
    return {
        "export_metadata": {"account_label": "Agentic ••••3436"},
        "raw_responses": {
            "portfolio": {"response": {key: {"data": {
                "cash": str(cash),
                "buying_power": {"buying_power": str(bp)},
            }}}},
            "positions": {"response": {key: {"data": {
                "positions": positions
            }}}},
        },
        "derived_position_valuations": valuations,
    }


def _submission() -> dict:
    return {
        "decision_hash": "abc",
        "matches": [
            {
                "ticker": "AAA",
                "status": "filled",
                "filled_quantity": 0.01,
            },
            {
                "ticker": "BBB",
                "status": "filled",
                "filled_quantity": 0.02,
            },
        ],
    }


def test_reconciles_position_deltas_and_ready_portfolio() -> None:
    result = reconcile_post_fill_portfolio(
        _submission(),
        _state(0.0, 0.0, 100.0, 100.0),
        _state(0.01, 0.02, 98.0, 98.0),
    )
    assert result.reconciled
    assert result.portfolio_state_ready
    assert result.matched_position_deltas == 2


def test_direct_mcp_structured_content_envelope_is_accepted() -> None:
    result = reconcile_post_fill_portfolio(
        _submission(),
        _state(0.0, 0.0, 100.0, 100.0, snake_case=True),
        _state(0.01, 0.02, 98.0, 98.0, snake_case=True),
    )
    assert result.reconciled
    assert result.portfolio_state_ready


def test_existing_position_uses_quantity_delta() -> None:
    result = reconcile_post_fill_portfolio(
        _submission(),
        _state(1.0, 2.0, 100.0, 100.0),
        _state(1.01, 2.02, 98.0, 98.0),
    )
    assert result.reconciled


def test_position_delta_mismatch_fails_closed() -> None:
    result = reconcile_post_fill_portfolio(
        _submission(),
        _state(0.0, 0.0, 100.0, 100.0),
        _state(0.02, 0.02, 98.0, 98.0),
    )
    assert not result.reconciled
    assert "position_delta_mismatch:AAA" in result.reasons


def test_nonfilled_submission_blocks_post_fill() -> None:
    submission = _submission()
    submission["matches"][1]["status"] = "accepted"
    result = reconcile_post_fill_portfolio(
        submission,
        _state(0.0, 0.0, 100.0, 100.0),
        _state(0.01, 0.0, 99.0, 99.0),
    )
    assert not result.reconciled
    assert "not_all_submission_orders_filled" in result.reasons
