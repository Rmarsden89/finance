from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.shadow.execution_gate import load_json
from finance.shadow.post_fill import reconcile_post_fill_portfolio
from finance.shadow.run_log import artifact_record, append_run_event, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile filled Agentic orders to post-fill broker positions."
    )
    parser.add_argument("--submission-reconciliation", type=Path, required=True)
    parser.add_argument("--pre-broker-state", type=Path, required=True)
    parser.add_argument("--post-broker-state", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/shadow/current/post_fill_reconciliation.json"),
    )
    parser.add_argument(
        "--portfolio-output",
        type=Path,
        default=Path("reports/shadow/current/portfolio_state.csv"),
    )
    parser.add_argument("--run-log", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    submission = load_json(args.submission_reconciliation)
    pre_state = load_json(args.pre_broker_state)
    post_state = load_json(args.post_broker_state)

    result = reconcile_post_fill_portfolio(submission, pre_state, post_state)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V1 POST-FILL RECONCILIATION")
    print(f"Decision hash:           {result.decision_hash}")
    print(f"Filled orders expected:  {result.expected_filled_orders}")
    print(f"Matched position deltas: {result.matched_position_deltas}")
    print(f"Post positions:          {result.post_positions}")
    print(f"Valued post positions:   {result.valued_post_positions}")
    print(f"Cash change:             {result.cash_change:,.2f}")
    print(f"Buying power change:     {result.buying_power_change:,.2f}")
    print(f"RECONCILED:              {'YES' if result.reconciled else 'NO'}")
    print(
        f"PORTFOLIO STATE READY:   "
        f"{'YES' if result.portfolio_state_ready else 'NO'}"
    )
    if result.reasons:
        print("Reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")

    if result.portfolio_state_ready:
        raw_positions = (
            post_state["raw_responses"]["positions"]["response"]
            ["structuredContent"]["data"]["positions"]
        )
        valuations = {
            str(row.get("symbol") or "").upper(): row
            for row in post_state.get("derived_position_valuations", [])
            if row.get("symbol")
        }
        rows = []
        for row in raw_positions:
            ticker = str(row.get("symbol") or "").upper()
            rows.append(
                {
                    "ticker": ticker,
                    "market_value": float(
                        valuations[ticker]["derived_market_value"]
                    ),
                }
            )
        args.portfolio_output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).sort_values("ticker").to_csv(
            args.portfolio_output, index=False
        )

    run_log = args.run_log or (args.output.parent / "run_log.jsonl")
    append_run_event(
        run_log,
        {
            "stage": "post_fill_reconciliation",
            "status": "success" if result.portfolio_state_ready else "blocked",
            "completed_at": utc_now_iso(),
            "decision_hash": result.decision_hash,
            "expected_filled_orders": result.expected_filled_orders,
            "matched_position_deltas": result.matched_position_deltas,
            "post_positions": result.post_positions,
            "valued_post_positions": result.valued_post_positions,
            "cash_change": result.cash_change,
            "buying_power_change": result.buying_power_change,
            "reasons": list(result.reasons),
            "inputs": {
                "submission_reconciliation": artifact_record(
                    args.submission_reconciliation
                ),
                "pre_broker_state": artifact_record(args.pre_broker_state),
                "post_broker_state": artifact_record(args.post_broker_state),
            },
            "outputs": {
                "post_fill_reconciliation": artifact_record(args.output),
                "portfolio_state": artifact_record(args.portfolio_output),
            },
        },
    )

    print(f"Output:                  {args.output}")
    if result.portfolio_state_ready:
        print(f"Portfolio state:         {args.portfolio_output}")
    print(f"Run log:                 {run_log}")


if __name__ == "__main__":
    main()
