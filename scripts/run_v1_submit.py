from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

from finance.broker.market_session import MarketSessionError, evaluate_nyse_session
from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, run
from finance.broker.robinhood_normalize import normalize_order_row
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
    parser.add_argument("--max-benchmark-quote-age-seconds", type=float, default=120.0)
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
    market_session_path = run_dir / "market_session_gate.json"
    benchmark_spy_path = run_dir / "benchmark_spy_capture.json"
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

    if receipt_path.exists():
        raise SystemExit(
            f"Submission receipt already exists: {receipt_path}. "
            "Blind retry is blocked; reconcile the existing receipt instead."
        )

    run_id = str(state.get("run_id") or f"long_growth_v1-{args.as_of.isoformat()}")
    session_attempt_id = str(uuid.uuid4())
    try:
        session_gate = evaluate_nyse_session(args.as_of, now=now)
    except MarketSessionError as exc:
        append_run_event(
            run_log,
            {
                "run_id": run_id,
                "attempt_id": session_attempt_id,
                "stage": "market_session_gate",
                "status": "error",
                "completed_at": utc_now_iso(),
                "decision_hash": decision_hash,
                "error": str(exc),
            },
        )
        raise SystemExit(f"Approved submission blocked: {exc}") from exc

    write_json(market_session_path, session_gate.to_dict())
    state["artifacts"] = {
        **(state.get("artifacts") or {}),
        "market_session_gate": str(market_session_path),
    }
    write_json(state_path, state)
    append_run_event(
        run_log,
        {
            "run_id": run_id,
            "attempt_id": session_attempt_id,
            "stage": "market_session_gate",
            "status": "ready" if session_gate.ready else "blocked",
            "completed_at": utc_now_iso(),
            "decision_hash": decision_hash,
            "session": session_gate.to_dict(),
            "outputs": {"market_session_gate": artifact_record(market_session_path)},
        },
    )

    if not session_gate.ready:
        window = (
            f"{session_gate.market_open} -> {session_gate.market_close}"
            if session_gate.market_open and session_gate.market_close
            else "no NYSE regular session"
        )
        raise SystemExit(
            "Approved submission blocked by NYSE market-session gate: "
            f"{session_gate.reason}; session={window}"
        )

    print(f"NYSE regular session: {session_gate.market_open} -> {session_gate.market_close}")
    print(f"Early close:          {'YES' if session_gate.early_close else 'NO'}")

    # Evaluation-only benchmark capture. This happens before any order placement,
    # so a missing/stale benchmark can fail closed without creating broker ambiguity.
    benchmark_gateway = RobinhoodBrokerGateway()
    benchmark_capture_started_at = utc_now_iso()

    async def capture_spy(session_client):
        scoped = RobinhoodBrokerGateway(client=session_client)
        rows = await scoped.get_quotes(["SPY"])
        return rows

    try:
        spy_quotes = run(
            benchmark_gateway.client.run_with_session(capture_spy)
        )
    except BaseException as exc:
        raise SystemExit(
            f"Approved submission blocked before order placement: SPY benchmark capture failed: {exc}"
        ) from exc

    if len(spy_quotes) != 1:
        raise SystemExit(
            "Approved submission blocked before order placement: "
            f"expected exactly one SPY quote, received {len(spy_quotes)}"
        )

    spy_quote = spy_quotes[0]
    spy_price, spy_quote_timestamp, spy_price_field = (
        RobinhoodBrokerGateway.select_latest_price(spy_quote)
    )
    if spy_price is None or not spy_quote_timestamp or not spy_price_field:
        raise SystemExit(
            "Approved submission blocked before order placement: "
            "SPY quote did not contain a usable timestamped trade price"
        )

    benchmark_captured_at = utc_now_iso()
    benchmark_age_seconds = (
        parse_utc(benchmark_captured_at) - parse_utc(spy_quote_timestamp)
    ).total_seconds()
    if benchmark_age_seconds < -1.0 or benchmark_age_seconds > args.max_benchmark_quote_age_seconds:
        raise SystemExit(
            "Approved submission blocked before order placement: "
            f"SPY benchmark quote age is {benchmark_age_seconds:.1f}s; "
            f"maximum allowed is {args.max_benchmark_quote_age_seconds:.1f}s"
        )

    benchmark_capture = {
        "symbol": "SPY",
        "source": "robinhood_trading_mcp",
        "evaluation_only": True,
        "capture_started_at": benchmark_capture_started_at,
        "captured_at": benchmark_captured_at,
        "price": spy_price,
        "price_field": spy_price_field,
        "quote_timestamp": spy_quote_timestamp,
        "quote_age_seconds": benchmark_age_seconds,
        "bid_price": spy_quote.get("bid_price"),
        "ask_price": spy_quote.get("ask_price"),
        "market_session_gate": session_gate.to_dict(),
    }
    write_json(benchmark_spy_path, benchmark_capture)
    state["artifacts"] = {
        **(state.get("artifacts") or {}),
        "benchmark_spy_capture": str(benchmark_spy_path),
    }
    write_json(state_path, state)
    append_run_event(
        run_log,
        {
            "run_id": run_id,
            "attempt_id": session_attempt_id,
            "stage": "evaluation_benchmark_capture",
            "status": "success",
            "completed_at": benchmark_captured_at,
            "decision_hash": decision_hash,
            "benchmark": benchmark_capture,
            "outputs": {
                "benchmark_spy_capture": artifact_record(benchmark_spy_path),
            },
        },
    )
    print(
        f"SPY benchmark:        {spy_price:.4f} at {spy_quote_timestamp} "
        f"(age {benchmark_age_seconds:.1f}s)"
    )

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

            broker_order = placed.get("order") if isinstance(placed, dict) else None
            row = normalize_order_row(broker_order) if isinstance(broker_order, dict) else {}
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
        "market_session_gate": session_gate.to_dict(),
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
                "market_session_gate": artifact_record(market_session_path),
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
