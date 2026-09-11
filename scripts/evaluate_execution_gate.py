from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance.shadow.execution_gate import evaluate_execution_gate, load_json
from finance.shadow.run_log import artifact_record, append_run_event, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-closed broker reconciliation gate for a V1 Agentic shadow decision."
    )
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--broker-state", type=Path, required=True)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/shadow/current/execution_gate.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    decision = load_json(args.decision)
    broker_state = load_json(args.broker_state)
    result = evaluate_execution_gate(decision, broker_state)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V1 EXECUTION GATE")
    print(f"Account:                  {result.account_label}")
    print(f"Decision date:            {result.decision_date}")
    print(f"Planned investment:       ${result.planned_investment:,.2f}")
    print(f"Buying power:             ${result.buying_power:,.2f}")
    print(f"Broker cash:              ${result.broker_cash:,.2f}")
    print(f"Broker positions:         {result.broker_positions}")
    print(f"Non-final equity orders:  {result.non_final_equity_orders}")
    print(
        f"Selected tradable:        "
        f"{result.selected_tradable}/{result.selected_buy_orders}"
    )
    print(f"READY:                    {'YES' if result.ready else 'NO'}")
    if result.reasons:
        print("Reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")
    print(f"Output:                   {args.output}")

    run_log = args.run_log or (args.output.parent / "run_log.jsonl")
    append_run_event(
        run_log,
        {
            "stage": "execution_gate",
            "status": "success" if result.ready else "blocked",
            "completed_at": utc_now_iso(),
            "decision_hash": result.decision_hash,
            "planned_investment": result.planned_investment,
            "reasons": list(result.reasons),
            "inputs": {
                "decision": artifact_record(args.decision),
                "broker_state": artifact_record(args.broker_state),
            },
            "outputs": {
                "execution_gate": artifact_record(args.output),
            },
        },
    )
    print(f"Run log:                  {run_log}")


if __name__ == "__main__":
    main()
