from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, run
from finance.shadow.order_review import validate_order_reviews
from finance.shadow.run_log import append_run_event, artifact_record, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen long_growth_v1 pre-submit package and, only with --approve, "
            "submit the exact reviewed Robinhood orders. Without --approve this command is dry-run only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--max-presubmit-age-minutes", type=float, default=5.0)
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> None:
    args = parse_args()
    repo = args.repo_root.resolve()
    run_dir = repo / "reports" / "shadow" / args.as_of.isoformat()
    state_path = run_dir / "workflow_state.json"
    intents_path = run_dir / "order_intents.json"
    gate_path = run_dir / "pre_submit_gate.json"
    reviews_path = run_dir / "order_reviews.json"
    receipt_path = run_dir / "broker_submission_receipt.json"
    reconciliation_path = run_dir / "submission_reconciliation.json"
    run_log = run_dir / "run_log.jsonl"

    for path in (state_path, intents_path, gate_path, reviews_path):
        if not path.exists():
            raise SystemExit(f"Missing required artifact: {path}")

    state = read_json(state_path)
    intents_payload = read_json(intents_path)
    gate = read_json(gate_path)
    reviews_payload = read_json(reviews_path)

    if state.get("status") != "AWAITING_APPROVAL":
        raise SystemExit(f"Workflow is not awaiting approval: status={state.get('status')!r}")

    decision_hash = str(intents_payload.get("decision_hash") or "")
    intents = intents_payload.get("intents") or []
    if not decision_hash or not intents:
        raise SystemExit("Frozen order intents are missing decision data")
    if float(intents_payload.get("total_dollars") or 0.0) > 10.0 + 1e-9:
        raise SystemExit("V1 submission total exceeds the $10 hard maximum")
    if int(intents_payload.get("order_count") or 0) != len(intents):
        raise SystemExit("Order intent count mismatch")
    if any(str(row.get("decision_hash") or "") != decision_hash for row in intents):
        raise SystemExit("Intent decision hash mismatch")

    if not gate.get("ready"):
        raise SystemExit("Pre-submit gate is not ready")
    if str(gate.get("decision_hash") or "") != decision_hash:
        raise SystemExit("Pre-submit decision hash mismatch")

    reviews = reviews_payload.get("reviews") or []
    if str(reviews_payload.get("decision_hash") or "") != decision_hash:
        raise SystemExit("Order review decision hash mismatch")
    validation = validate_order_reviews(intents, reviews)
    if not validation.ready:
        raise SystemExit("Order reviews are no longer valid: " + ", ".join(validation.reasons))

    snapshot_created_at = str(gate.get("snapshot_created_at") or "")
    if not snapshot_created_at:
        raise SystemExit("Pre-submit gate is missing snapshot timestamp")
    now = datetime.now(timezone.utc)
    age_minutes = (now - parse_utc(snapshot_created_at)).total_seconds() / 60.0
    if age_minutes < -1.0:
        raise SystemExit("Pre-submit snapshot timestamp is in the future")
    if age_minutes > args.max_presubmit_age_minutes:
        raise SystemExit(
            f"Pre-submit package is stale ({age_minutes:.2f} minutes). "
            f"Rerun: py scripts\\run_v1_presubmit.py --as-of {args.as_of.isoformat()}"
        )

    print("V1 SUBMISSION PACKAGE")
    print(f"Decision hash:        {decision_hash}")
    print(f"Order count:          {len(intents)}")
    print(f"Total dollars:        ${float(intents_payload.get('total_dollars') or 0.0):.2f}")
    print(f"Pre-submit age:       {age_minutes:.2f} minutes")
    print(f"Reviews clean:        {validation.clean_count}/{validation.reviewed_count}")

    if not args.approve:
        print()
        print("DRY RUN ONLY: no orders were placed.")
        print("Use --approve only during the intended regular-session submission window.")
        return

    if args.as_of.weekday() >= 5:
        raise SystemExit(
            f"Approved submission blocked: {args.as_of.isoformat()} is a weekend. "
            "Run a fresh preparation + pre-submit cycle on the intended regular market session."
        )
    if datetime.now().astimezone().date() != args.as_of:
        raise SystemExit("Approved submission blocked: --as-of must equal today's local date")

    if receipt_path.exists():
        raise SystemExit(
            f"Submission receipt already exists: {receipt_path}. "
            "Blind retry is blocked; reconcile the existing receipt instead."
        )

    run_id = str(state.get("run_id") or f"long_growth_v1-{args.as_of.isoformat()}")
    attempt_id = str(uuid.uuid4())
    state["submission_attempt_id"] = attempt_id
    state["status"] = "SUBMISSION_RUNNING"
    write_json(state_path, state)

    gateway = RobinhoodBrokerGateway()

    async def submit_all(session_client):
        scoped = RobinhoodBrokerGateway(client=session_client)
        account, _ = await scoped.get_agentic_account()
        submitted_orders = []
        for intent in intents:
            ticker = str(intent.get("ticker") or "").upper()
            try:
                placed = await scoped.place_equity_order(
                    account_number=account.account_number,
                    intent=intent,
                )
            except BaseException as exc:
                return submitted_orders, ticker, exc

            data = placed.get("data") if isinstance(placed, dict) else None
            row = dict(data) if isinstance(data, dict) else {}
            row.setdefault("ticker", ticker)
            row.setdefault("symbol", ticker)
            row.setdefault("side", str(intent.get("side") or "buy"))
            row.setdefault("requested_dollars", float(intent.get("amount_dollars") or 0.0))
            row["idempotency_key"] = str(intent.get("idempotency_key") or "")
            row["decision_hash"] = decision_hash
            submitted_orders.append(row)

        return submitted_orders, None, None

    submitted_orders, failed_ticker, submission_error = run(
        gateway.client.run_with_session(submit_all)
    )

    receipt = {
        "decision_hash": decision_hash,
        "submitted_at": utc_now_iso(),
        "submitted_orders": submitted_orders,
        "submitted_count": len(submitted_orders),
        "expected_count": len(intents),
        "failed_ticker": failed_ticker,
        "submission_error": str(submission_error) if submission_error is not None else None,
        "blind_retry_blocked": True,
    }
    write_json(receipt_path, receipt)

    append_run_event(
        run_log,
        {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "stage": "submission",
            "status": "submitted" if submission_error is None else "partial_or_ambiguous",
            "completed_at": utc_now_iso(),
            "decision_hash": decision_hash,
            "submitted_count": len(submitted_orders),
            "expected_count": len(intents),
            "failed_ticker": failed_ticker,
            "inputs": {
                "order_intents": artifact_record(intents_path),
                "pre_submit_gate": artifact_record(gate_path),
                "order_reviews": artifact_record(reviews_path),
            },
            "outputs": {"broker_submission_receipt": artifact_record(receipt_path)},
        },
    )

    state["artifacts"] = {
        **(state.get("artifacts") or {}),
        "broker_submission_receipt": str(receipt_path),
    }
    if submission_error is not None:
        state["status"] = "SUBMISSION_REQUIRES_RECONCILIATION"
        state["submission_error"] = str(submission_error)
        write_json(state_path, state)
        raise SystemExit(
            f"Submission stopped at {failed_ticker}; {len(submitted_orders)} order(s) already returned broker data. "
            "Blind retry is blocked. Reconcile the saved receipt before any further action."
        )

    subprocess.run(
        [
            sys.executable,
            "scripts/reconcile_submission_receipt.py",
            "--order-intents",
            str(intents_path),
            "--broker-receipt",
            str(receipt_path),
            "--output",
            str(reconciliation_path),
            "--run-log",
            str(run_log),
        ],
        cwd=repo,
        check=True,
    )

    reconciliation = read_json(reconciliation_path)
    state["artifacts"]["submission_reconciliation"] = str(reconciliation_path)
    state["status"] = (
        "SUBMITTED_RECONCILED"
        if reconciliation.get("reconciled") and reconciliation.get("all_accepted")
        else "SUBMISSION_REQUIRES_RECONCILIATION"
    )
    write_json(state_path, state)

    print()
    print("=" * 72)
    print("V1 SUBMISSION COMPLETE")
    print(f"Status:               {state['status']}")
    print(f"Submitted orders:     {len(submitted_orders)}/{len(intents)}")
    print(f"Receipt:              {receipt_path}")
    print(f"Reconciliation:       {reconciliation_path}")
    print("=" * 72)

    if state["status"] == "SUBMITTED_RECONCILED":
        print()
        print("Running first post-fill verification...", flush=True)
        subprocess.run(
            [
                sys.executable,
                "scripts/run_v1_postfill.py",
                "--as-of",
                args.as_of.isoformat(),
                "--repo-root",
                str(repo),
            ],
            cwd=repo,
            check=True,
        )


if __name__ == "__main__":
    main()
