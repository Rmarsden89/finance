from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance.shadow.execution_gate import load_json
from finance.shadow.pre_submit import evaluate_pre_submit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the final fail-closed pre-submit gate against a fresh Agentic broker snapshot."
    )
    parser.add_argument("--order-intents", type=Path, required=True)
    parser.add_argument("--broker-state", type=Path, required=True)
    parser.add_argument("--max-snapshot-age-minutes", type=float, default=5.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/shadow/current/pre_submit_gate.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    intents = load_json(args.order_intents)
    broker = load_json(args.broker_state)
    result = evaluate_pre_submit(
        intents,
        broker,
        max_snapshot_age_minutes=args.max_snapshot_age_minutes,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V1 PRE-SUBMIT GATE")
    print(f"Decision hash:          {result.decision_hash}")
    print(f"Order count:            {result.order_count}")
    print(f"Total dollars:          \${result.total_dollars:,.2f}")
    print(f"Buying power:           \${result.buying_power:,.2f}")
    print(f"Snapshot age:           {result.snapshot_age_minutes:.2f} minutes")
    print(f"Non-final orders:       {result.non_final_equity_orders}")
    print(f"Tradable intents:       {result.tradable_count}/{result.order_count}")
    print(f"READY TO SUBMIT:        {'YES' if result.ready else 'NO'}")
    if result.reasons:
        print("Reasons:")
        for reason in result.reasons:
            print(f"  - {reason}")
    print(f"Output:                 {args.output}")


if __name__ == "__main__":
    main()
