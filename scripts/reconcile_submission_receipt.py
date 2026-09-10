from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance.shadow.execution_gate import load_json
from finance.shadow.run_log import artifact_record, append_run_event, utc_now_iso
from finance.shadow.submission_receipt import reconcile_submission_receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile Robinhood Agentic submission receipts to the frozen V1 order intents."
    )
    parser.add_argument("--order-intents", type=Path, required=True)
    parser.add_argument("--broker-receipt", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/shadow/current/submission_reconciliation.json"),
    )
    parser.add_argument("--run-log", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    intents = load_json(args.order_intents)
    receipt = load_json(args.broker_receipt)

    result = reconcile_submission_receipt(intents, receipt)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V1 SUBMISSION RECONCILIATION")
    print(f"Decision hash:       {result.decision_hash}")
    print(f"Expected orders:     {result.expected_orders}")
    print(f"Broker orders seen:  {result.broker_orders_seen}")
    print(f"Matched orders:      {result.matched_orders}")
    print(f"Accepted orders:     {result.accepted_orders}")
    print(f"Filled orders:       {result.filled_orders}")
    print(f"Failed orders:       {result.failed_orders}")
    print(f"Missing orders:      {result.missing_orders}")
    print(f"Duplicate matches:   {result.duplicate_matches}")
    print(f"Unexpected orders:   {result.unexpected_orders}")
    print(f"RECONCILED:          {'YES' if result.reconciled else 'NO'}")
    print(f"ALL ACCEPTED:        {'YES' if result.all_accepted else 'NO'}")
    print(f"BLIND RETRY BLOCKED: {'YES' if result.retry_blocked else 'NO'}")
    if result.reasons:
        print("Reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")

    print()
    for row in result.matches:
        print(
            f"{row.ticker:<6} "
            f"requested={row.requested_dollars:,.2f} "
            f"state={row.broker_state or '-':<16} "
            f"order_id={row.broker_order_id or '-'} "
            f"status={row.status}"
        )

    run_log = args.run_log or (args.output.parent / "run_log.jsonl")
    append_run_event(
        run_log,
        {
            "stage": "submission_reconciliation",
            "status": (
                "success"
                if result.all_accepted
                else "reconciled_with_failures"
                if result.reconciled
                else "failed_reconciliation"
            ),
            "completed_at": utc_now_iso(),
            "decision_hash": result.decision_hash,
            "expected_orders": result.expected_orders,
            "broker_orders_seen": result.broker_orders_seen,
            "matched_orders": result.matched_orders,
            "accepted_orders": result.accepted_orders,
            "filled_orders": result.filled_orders,
            "failed_orders": result.failed_orders,
            "missing_orders": result.missing_orders,
            "duplicate_matches": result.duplicate_matches,
            "unexpected_orders": result.unexpected_orders,
            "retry_blocked": result.retry_blocked,
            "reasons": list(result.reasons),
            "inputs": {
                "order_intents": artifact_record(args.order_intents),
                "broker_receipt": artifact_record(args.broker_receipt),
            },
            "outputs": {
                "submission_reconciliation": artifact_record(args.output),
            },
        },
    )

    print(f"Output:               {args.output}")
    print(f"Run log:              {run_log}")


if __name__ == "__main__":
    main()
