from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import uuid

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, run
from finance.shadow.run_log import append_run_event, artifact_record, utc_now_iso
from finance.shadow.submission_receipt import reconcile_submission_receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh exact submitted Robinhood orders plus account state, then verify fills "
            "and reconcile post-fill portfolio state. This command never places orders."
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
    pre_broker_path = run_dir / "broker_snapshot_presubmit.json"
    initial_receipt_path = run_dir / "broker_submission_receipt.json"
    current_receipt_path = run_dir / "broker_submission_receipt_postfill.json"
    post_broker_path = run_dir / "broker_snapshot_postfill.json"
    submission_recon_path = run_dir / "submission_reconciliation_postfill.json"
    post_fill_recon_path = run_dir / "post_fill_reconciliation.json"
    portfolio_path = run_dir / "portfolio_state.csv"
    run_log = run_dir / "run_log.jsonl"

    for path in (state_path, intents_path, pre_broker_path, initial_receipt_path):
        if not path.exists():
            raise SystemExit(f"Missing required artifact: {path}")

    state = read_json(state_path)
    if state.get("status") not in {
        "SUBMITTED_RECONCILED",
        "POSTFILL_PENDING",
        "POSTFILL_RECONCILIATION_REQUIRED",
    }:
        raise SystemExit(
            "Workflow is not ready for post-fill verification: "
            f"status={state.get('status')!r}"
        )

    intents = read_json(intents_path)
    initial_receipt = read_json(initial_receipt_path)
    submitted = initial_receipt.get("submitted_orders") or []
    expected_count = int(intents.get("order_count") or 0)
    if len(submitted) != expected_count:
        raise SystemExit(
            f"Submission receipt count mismatch: {len(submitted)} != {expected_count}"
        )

    order_ids: list[str] = []
    for row in submitted:
        order_id = str(row.get("order_id") or row.get("id") or "").strip()
        if not order_id:
            raise SystemExit("Submission receipt contains an order without broker order ID")
        order_ids.append(order_id)
    if len(set(order_ids)) != len(order_ids):
        raise SystemExit("Submission receipt contains duplicate broker order IDs")

    run_id = str(state.get("run_id") or f"long_growth_v1-{args.as_of.isoformat()}")
    attempt_id = str(uuid.uuid4())
    state["postfill_attempt_id"] = attempt_id
    state["status"] = "POSTFILL_RUNNING"
    write_json(state_path, state)

    gateway = RobinhoodBrokerGateway()

    async def refresh_all(session_client):
        scoped = RobinhoodBrokerGateway(client=session_client)
        account, _ = await scoped.get_agentic_account()
        current_orders = []
        for order_id in order_ids:
            row = await scoped.get_equity_order_by_id(
                account_number=account.account_number,
                order_id=order_id,
            )
            if row is None:
                current_orders.append({"id": order_id, "state": "missing"})
            else:
                current_orders.append(row)
        snapshot = await scoped.get_account_snapshot()
        return current_orders, snapshot

    try:
        current_orders, post_snapshot = run(
            gateway.client.run_with_session(refresh_all)
        )
        write_json(post_broker_path, post_snapshot)

        current_receipt = {
            "decision_hash": intents.get("decision_hash"),
            "refreshed_at": utc_now_iso(),
            "submitted_orders": current_orders,
            "submitted_count": len(current_orders),
            "expected_count": expected_count,
            "source_receipt": str(initial_receipt_path),
        }
        write_json(current_receipt_path, current_receipt)

        submission_recon = reconcile_submission_receipt(intents, current_receipt)
        write_json(submission_recon_path, submission_recon.to_dict())

        append_run_event(
            run_log,
            {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "stage": "postfill_order_refresh",
                "status": "success",
                "completed_at": utc_now_iso(),
                "decision_hash": intents.get("decision_hash"),
                "filled_orders": submission_recon.filled_orders,
                "expected_orders": submission_recon.expected_orders,
                "inputs": {
                    "initial_receipt": artifact_record(initial_receipt_path),
                    "order_intents": artifact_record(intents_path),
                },
                "outputs": {
                    "postfill_receipt": artifact_record(current_receipt_path),
                    "postfill_broker_snapshot": artifact_record(post_broker_path),
                    "postfill_submission_reconciliation": artifact_record(submission_recon_path),
                },
            },
        )

        if submission_recon.filled_orders != submission_recon.expected_orders:
            state["status"] = "POSTFILL_PENDING"
            state["artifacts"] = {
                **(state.get("artifacts") or {}),
                "broker_snapshot_postfill": str(post_broker_path),
                "submission_reconciliation_postfill": str(submission_recon_path),
            }
            write_json(state_path, state)
            print()
            print("=" * 72)
            print("V1 POST-FILL CHECK")
            print(f"Status:                  {state['status']}")
            print(
                f"Filled orders:           {submission_recon.filled_orders}/"
                f"{submission_recon.expected_orders}"
            )
            print("No retry or new orders were placed.")
            print(
                f"Rerun: py scripts\\run_v1_postfill.py --as-of {args.as_of.isoformat()}"
            )
            print("=" * 72)
            return

        subprocess.run(
            [
                sys.executable,
                "scripts/reconcile_post_fill_portfolio.py",
                "--submission-reconciliation",
                str(submission_recon_path),
                "--pre-broker-state",
                str(pre_broker_path),
                "--post-broker-state",
                str(post_broker_path),
                "--output",
                str(post_fill_recon_path),
                "--portfolio-output",
                str(portfolio_path),
                "--run-log",
                str(run_log),
            ],
            cwd=repo,
            check=True,
        )

        post_recon = read_json(post_fill_recon_path)
        state["artifacts"] = {
            **(state.get("artifacts") or {}),
            "broker_snapshot_postfill": str(post_broker_path),
            "submission_reconciliation_postfill": str(submission_recon_path),
            "post_fill_reconciliation": str(post_fill_recon_path),
            "portfolio_state": str(portfolio_path),
        }
        if post_recon.get("portfolio_state_ready"):
            state["status"] = "COMPLETE"
            state["postfill_reasons"] = []
        else:
            state["status"] = "POSTFILL_RECONCILIATION_REQUIRED"
            state["postfill_reasons"] = post_recon.get("reasons") or []
        write_json(state_path, state)

        print()
        print("=" * 72)
        print("V1 POST-FILL VERIFICATION COMPLETE")
        print(f"Status:                  {state['status']}")
        print(
            f"Filled orders:           {submission_recon.filled_orders}/"
            f"{submission_recon.expected_orders}"
        )
        print(
            f"Portfolio reconciled:    "
            f"{'YES' if post_recon.get('portfolio_state_ready') else 'NO'}"
        )
        print(f"Post-fill snapshot:      {post_broker_path}")
        print(f"Post-fill reconciliation:{post_fill_recon_path}")
        print("=" * 72)

    except BaseException as exc:
        if state.get("status") == "POSTFILL_RUNNING":
            state["status"] = "POSTFILL_RECONCILIATION_REQUIRED"
            state["postfill_error"] = str(exc)
            write_json(state_path, state)
        raise


if __name__ == "__main__":
    main()
