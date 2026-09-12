from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
import uuid

from finance.broker.robinhood_gateway import RobinhoodBrokerGateway, run
from finance.shadow.order_review import validate_order_reviews
from finance.shadow.pre_submit import evaluate_pre_submit
from finance.shadow.run_log import append_run_event, artifact_record, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh Agentic broker state, run the <=5-minute pre-submit gate, "
            "and review every frozen V1 intent with Robinhood. This command "
            "never places orders."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--max-snapshot-age-minutes", type=float, default=5.0)
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
    run_log = run_dir / "run_log.jsonl"
    broker_path = run_dir / "broker_snapshot_presubmit.json"
    gate_path = run_dir / "pre_submit_gate.json"
    reviews_path = run_dir / "order_reviews.json"

    for path in (state_path, intents_path):
        if not path.exists():
            raise SystemExit(f"Missing required artifact: {path}")

    state = read_json(state_path)
    if state.get("status") != "READY_FOR_PRESUBMIT_REFRESH":
        raise SystemExit(
            "Workflow is not ready for pre-submit refresh: "
            f"status={state.get('status')!r}"
        )
    if state.get("order_submission_enabled") is not False:
        raise SystemExit("Preparation state does not explicitly disable order submission")

    intents_payload = read_json(intents_path)
    intents = intents_payload.get("intents") or []
    if not intents:
        raise SystemExit("No order intents found")
    if int(intents_payload.get("order_count") or 0) != len(intents):
        raise SystemExit("Order intent count does not match intent rows")
    if float(intents_payload.get("total_dollars") or 0.0) > 10.0 + 1e-9:
        raise SystemExit("V1 pre-submit total exceeds the $10 hard maximum")

    decision_hash = str(intents_payload.get("decision_hash") or "")
    if not decision_hash:
        raise SystemExit("Order intents are missing decision_hash")
    if any(str(row.get("decision_hash") or "") != decision_hash for row in intents):
        raise SystemExit("Order intents contain a decision hash mismatch")

    tickers = [str(row.get("ticker") or "").upper() for row in intents]
    if any(not ticker for ticker in tickers) or len(set(tickers)) != len(tickers):
        raise SystemExit("Order intents contain missing or duplicate tickers")

    run_id = str(state.get("run_id") or f"long_growth_v1-{args.as_of.isoformat()}")
    attempt_id = str(uuid.uuid4())
    state["presubmit_attempt_id"] = attempt_id
    state["status"] = "PRESUBMIT_RUNNING"
    write_json(state_path, state)

    gateway = RobinhoodBrokerGateway()

    async def presubmit_all(session_client):
        scoped = RobinhoodBrokerGateway(client=session_client)
        print("Fetching fresh Agentic pre-submit snapshot...", flush=True)
        broker_snapshot = await scoped.get_account_snapshot(tradability_symbols=tickers)
        write_json(broker_path, broker_snapshot)

        gate = evaluate_pre_submit(
            intents_payload,
            broker_snapshot,
            max_snapshot_age_minutes=args.max_snapshot_age_minutes,
        )
        write_json(gate_path, gate.to_dict())
        if not gate.ready:
            return broker_snapshot, gate, [], None

        account, _ = await scoped.get_agentic_account()
        reviews = []
        print(f"Reviewing {len(intents)} frozen intents with Robinhood...", flush=True)
        for intent in intents:
            reviewed = await scoped.review_equity_order(
                account_number=account.account_number,
                intent=intent,
            )
            reviews.append(
                {
                    "ticker": str(intent["ticker"]).upper(),
                    "rank": intent.get("rank"),
                    "decision_hash": decision_hash,
                    "idempotency_key": intent.get("idempotency_key"),
                    "data": reviewed.get("data"),
                    "raw": reviewed.get("raw"),
                }
            )
        validation = validate_order_reviews(intents, reviews)
        write_json(
            reviews_path,
            {
                "decision_hash": decision_hash,
                "review_count": len(reviews),
                "reviews": reviews,
                "validation": validation.to_dict(),
            },
        )
        return broker_snapshot, gate, reviews, validation

    try:
        broker_snapshot, gate, reviews, validation = run(
            gateway.client.run_with_session(presubmit_all)
        )

        append_run_event(
            run_log,
            {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "stage": "pre_submit_refresh_and_review",
                "status": (
                    "success"
                    if gate.ready and validation is not None and validation.ready
                    else "blocked"
                ),
                "completed_at": utc_now_iso(),
                "decision_hash": decision_hash,
                "snapshot_age_minutes": gate.snapshot_age_minutes,
                "order_count": len(intents),
                "reviewed_count": len(reviews),
                "reasons": (
                    list(gate.reasons)
                    if not gate.ready
                    else list(validation.reasons) if validation is not None else ["review_validation_missing"]
                ),
                "inputs": {"order_intents": artifact_record(intents_path)},
                "outputs": {
                    "broker_snapshot_presubmit": artifact_record(broker_path),
                    "pre_submit_gate": artifact_record(gate_path),
                    "order_reviews": artifact_record(reviews_path) if reviews_path.exists() else None,
                },
            },
        )

        if not gate.ready:
            state["status"] = "PRESUBMIT_BLOCKED"
            state["presubmit_reasons"] = list(gate.reasons)
            write_json(state_path, state)
            raise SystemExit(
                "Pre-submit gate failed closed: " + ", ".join(gate.reasons)
            )

        if validation is None or not validation.ready:
            reasons = list(validation.reasons) if validation is not None else ["review_validation_missing"]
            state["status"] = "PRESUBMIT_BLOCKED"
            state["presubmit_reasons"] = reasons
            write_json(state_path, state)
            raise SystemExit(
                "Robinhood order review failed closed: " + ", ".join(reasons)
            )

        state["status"] = "AWAITING_APPROVAL"
        state["presubmit_reasons"] = []
        state["artifacts"] = {
            **(state.get("artifacts") or {}),
            "broker_snapshot_presubmit": str(broker_path),
            "pre_submit_gate": str(gate_path),
            "order_reviews": str(reviews_path),
        }
        write_json(state_path, state)

        print()
        print("=" * 72)
        print("V1 PRE-SUBMIT REVIEW COMPLETE")
        print(f"Status:                     {state['status']}")
        print(f"Decision hash:              {decision_hash}")
        print(f"Fresh snapshot age:         {gate.snapshot_age_minutes:.2f} minutes")
        print(f"Buying power:               ${gate.buying_power:,.2f}")
        print(f"Tradable intents:           {gate.tradable_count}/{gate.order_count}")
        print(f"Robinhood reviews clean:    {validation.clean_count}/{validation.reviewed_count}")
        print(f"Pre-submit gate:            {gate_path}")
        print(f"Order reviews:              {reviews_path}")
        print()
        print("NO ORDERS WERE PLACED.")
        print("A separate approval/submission step is required.")
        print("=" * 72)
    except BaseException as exc:
        if state.get("status") == "PRESUBMIT_RUNNING":
            state["status"] = "PRESUBMIT_FAILED"
            state["presubmit_error"] = str(exc)
            write_json(state_path, state)
        raise


if __name__ == "__main__":
    main()
