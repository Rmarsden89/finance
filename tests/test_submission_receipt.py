from finance.shadow.submission_receipt import reconcile_submission_receipt


def _intents() -> dict:
    return {
        "decision_hash": "abc",
        "total_dollars": 2.0,
        "intents": [
            {
                "ticker": "AAA",
                "amount_dollars": 1.0,
                "idempotency_key": "k1",
            },
            {
                "ticker": "BBB",
                "amount_dollars": 1.0,
                "idempotency_key": "k2",
            },
        ],
    }


def test_reconciles_two_accepted_orders() -> None:
    receipt = {
        "submitted_orders": [
            {
                "symbol": "AAA",
                "side": "buy",
                "state": "queued",
                "id": "o1",
                "dollar_based_amount": {"amount": "1.00"},
                "created_at": "2026-09-10T14:00:00Z",
            },
            {
                "symbol": "BBB",
                "side": "buy",
                "state": "filled",
                "id": "o2",
                "dollar_based_amount": {"amount": "1.00"},
                "cumulative_quantity": "0.01",
                "average_price": "100",
                "created_at": "2026-09-10T14:00:01Z",
            },
        ]
    }

    result = reconcile_submission_receipt(_intents(), receipt)

    assert result.reconciled
    assert result.all_accepted
    assert result.accepted_orders == 2
    assert result.filled_orders == 1
    assert result.retry_blocked


def test_missing_order_fails_reconciliation_and_blocks_retry() -> None:
    receipt = {
        "submitted_orders": [
            {
                "symbol": "AAA",
                "side": "buy",
                "state": "queued",
                "id": "o1",
                "dollar_based_amount": {"amount": "1.00"},
            }
        ]
    }

    result = reconcile_submission_receipt(_intents(), receipt)

    assert not result.reconciled
    assert result.missing_orders == 1
    assert result.retry_blocked


def test_duplicate_matching_order_is_ambiguous() -> None:
    receipt = {
        "submitted_orders": [
            {
                "symbol": "AAA",
                "side": "buy",
                "state": "queued",
                "id": "o1",
                "dollar_based_amount": {"amount": "1.00"},
            },
            {
                "symbol": "AAA",
                "side": "buy",
                "state": "queued",
                "id": "o2",
                "dollar_based_amount": {"amount": "1.00"},
            },
            {
                "symbol": "BBB",
                "side": "buy",
                "state": "queued",
                "id": "o3",
                "dollar_based_amount": {"amount": "1.00"},
            },
        ]
    }

    result = reconcile_submission_receipt(_intents(), receipt)

    assert not result.reconciled
    assert result.duplicate_matches == 1
    assert result.retry_blocked


def test_rejected_order_reconciles_but_is_not_all_accepted() -> None:
    receipt = {
        "submitted_orders": [
            {
                "symbol": "AAA",
                "side": "buy",
                "state": "rejected",
                "id": "o1",
                "dollar_based_amount": {"amount": "1.00"},
            },
            {
                "symbol": "BBB",
                "side": "buy",
                "state": "filled",
                "id": "o2",
                "dollar_based_amount": {"amount": "1.00"},
            },
        ]
    }

    result = reconcile_submission_receipt(_intents(), receipt)

    assert result.reconciled
    assert not result.all_accepted
    assert result.failed_orders == 1
    assert result.retry_blocked
