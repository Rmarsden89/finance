from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from finance.shadow.execution_gate import load_json
from finance.shadow.order_intent import build_order_intents
from finance.shadow.run_log import artifact_record, append_run_event, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build broker-ready V1 order intents without placing orders."
    )
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--execution-gate", type=Path, required=True)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/shadow/current"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    decision = load_json(args.decision)
    gate = load_json(args.execution_gate)
    batch = build_order_intents(decision, gate)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "order_intents.json"
    csv_path = args.output_dir / "order_intents.csv"

    json_path.write_text(
        json.dumps(batch.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "rank",
            "ticker",
            "side",
            "order_type",
            "market_hours",
            "time_in_force",
            "amount_dollars",
            "decision_hash",
            "idempotency_key",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in batch.intents:
            writer.writerow(row.to_dict())

    print("V1 ORDER INTENTS")
    print(f"Decision hash:  {batch.decision_hash}")
    print(f"Order count:    {batch.order_count}")
    print(f"Total dollars:  ${batch.total_dollars:,.2f}")
    for row in batch.intents:
        print(
            f"{row.rank:>2}. {row.ticker:<6} "
            f"${row.amount_dollars:,.2f} "
            f"{row.order_type} {row.market_hours} "
            f"idempotency={row.idempotency_key[:12]}..."
        )
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")

    run_log = args.run_log or (args.output_dir / "run_log.jsonl")
    append_run_event(
        run_log,
        {
            "stage": "order_intents",
            "status": "success",
            "completed_at": utc_now_iso(),
            "decision_hash": batch.decision_hash,
            "order_count": batch.order_count,
            "total_dollars": batch.total_dollars,
            "inputs": {
                "decision": artifact_record(args.decision),
                "execution_gate": artifact_record(args.execution_gate),
            },
            "outputs": {
                "json": artifact_record(json_path),
                "csv": artifact_record(csv_path),
            },
        },
    )
    print(f"Run log: {run_log}")


if __name__ == "__main__":
    main()
