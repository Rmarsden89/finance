from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from finance.shadow.run_log import append_run_event, artifact_record, utc_now_iso
from finance.shadow.submission_receipt import reconcile_submission_receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only recovery for a V1 submission whose saved Robinhood placement "
            "receipt could not initially be reconciled. This command never places orders."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    repo = args.repo_root.resolve()
    run_dir = repo / "reports" / "shadow" / args.as_of.isoformat()
    state_path = run_dir / "workflow_state.json"
    intents_path = run_dir / "order_intents.json"
    receipt_path = run_dir / "broker_submission_receipt.json"
    reconciliation_path = run_dir / "submission_reconciliation.json"
    run_log = run_dir / "run_log.jsonl"

    for path in (state_path, intents_path, receipt_path):
        if not path.exists():
            raise SystemExit(f"Missing required artifact: {path}")

    state = read_json(state_path)
    if state.get("status") not in {
        "SUBMISSION_REQUIRES_RECONCILIATION",
        "SUBMISSION_RUNNING",
        "SUBMITTED_RECONCILED",
        "POSTFILL_PENDING",
    }:
        raise SystemExit(
            "Workflow is not in a submission-recovery state: "
            f"status={state.get('status')!r}"
        )

    intents = read_json(intents_path)
    receipt = read_json(receipt_path)
    result = reconcile_submission_receipt(intents, receipt)
    write_json(reconciliation_path, result.to_dict())

    append_run_event(
        run_log,
        {
            "run_id": state.get("run_id"),
            "attempt_id": state.get("submission_attempt_id") or state.get("attempt_id"),
            "stage": "submission_receipt_recovery",
            "status": "success" if result.all_accepted else "blocked",
            "completed_at": utc_now_iso(),
            "decision_hash": result.decision_hash,
            "matched_orders": result.matched_orders,
            "accepted_orders": result.accepted_orders,
            "filled_orders": result.filled_orders,
            "reasons": list(result.reasons),
            "inputs": {
                "order_intents": artifact_record(intents_path),
                "broker_submission_receipt": artifact_record(receipt_path),
            },
            "outputs": {
                "submission_reconciliation": artifact_record(reconciliation_path),
            },
        },
    )

    state.setdefault("artifacts", {})["submission_reconciliation"] = str(reconciliation_path)
    if result.reconciled and result.all_accepted:
        state["status"] = "SUBMITTED_RECONCILED"
        state.pop("submission_error", None)
    else:
        state["status"] = "SUBMISSION_REQUIRES_RECONCILIATION"
    write_json(state_path, state)

    print("V1 SUBMISSION RECEIPT RECOVERY")
    print(f"Decision hash:       {result.decision_hash}")
    print(f"Expected orders:     {result.expected_orders}")
    print(f"Broker orders seen:  {result.broker_orders_seen}")
    print(f"Matched orders:      {result.matched_orders}")
    print(f"Accepted orders:     {result.accepted_orders}")
    print(f"Filled orders:       {result.filled_orders}")
    print(f"RECONCILED:          {'YES' if result.reconciled else 'NO'}")
    print(f"ALL ACCEPTED:        {'YES' if result.all_accepted else 'NO'}")
    if result.reasons:
        print("Reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")
    print(f"Workflow status:     {state['status']}")
    print(f"Output:              {reconciliation_path}")
    print()
    print("NO ORDERS WERE PLACED BY THIS RECOVERY COMMAND.")
    if result.reconciled and result.all_accepted:
        print(
            "Next: py scripts\\run_v1_postfill.py "
            f"--as-of {args.as_of.isoformat()}"
        )


if __name__ == "__main__":
    main()
