from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from finance.shadow import build_shadow_decision_plan, load_portfolio_positions
from finance.shadow.run_log import artifact_record, append_run_event, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a broker-neutral weekly shadow decision artifact from the "
            "frozen long_growth_v1 output."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument(
        "--portfolio-state",
        type=Path,
        help=(
            "Optional CSV with ticker,market_value columns using current "
            "pre-contribution marked values."
        ),
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="Data/decision as-of date. Default: today.",
    )
    parser.add_argument(
        "--decision-date",
        type=date.fromisoformat,
        help=(
            "Optional exact signal decision date. Defaults to latest date "
            "on or before --as-of."
        ),
    )
    parser.add_argument("--starting-cash", type=float, default=0.0)
    parser.add_argument("--weekly-contribution", type=float, default=10.0)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument(
        "--max-addon-position-weight",
        type=float,
        default=0.10,
    )
    parser.add_argument("--max-signal-age-days", type=int, default=7)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/shadow/current"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    signals = pd.read_csv(args.long_growth, low_memory=False)
    positions = load_portfolio_positions(args.portfolio_state)

    plan = build_shadow_decision_plan(
        signals,
        as_of=args.as_of,
        positions=positions,
        starting_cash=args.starting_cash,
        weekly_contribution=args.weekly_contribution,
        top_n=args.top_n,
        max_addon_position_weight=args.max_addon_position_weight,
        decision_date=args.decision_date,
        max_signal_age_days=args.max_signal_age_days,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    json_path = args.output_dir / "shadow_decision.json"
    csv_path = args.output_dir / "shadow_decision.csv"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(plan.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")

    rows = []
    for row in plan.decisions:
        rows.append(
            {
                "decision_date": plan.decision_date,
                "as_of": plan.as_of,
                "model_id": plan.model_id,
                "decision_hash": plan.decision_hash,
                "rank": row.rank,
                "ticker": row.ticker,
                "score": row.score,
                "current_market_value": row.current_market_value,
                "pre_contribution_weight": row.pre_contribution_weight,
                "status": row.status,
                "allocation_dollars": row.allocation_dollars,
                "reason": row.reason,
            }
        )
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    print("SHADOW DECISION")
    print(f"Decision date:        {plan.decision_date}")
    print(f"As of:                {plan.as_of}")
    print(f"Model:                {plan.model_id}")
    print(f"Top N:                {plan.top_n}")
    print(
        f"Portfolio value:      {plan.pre_contribution_portfolio_value:,.2f}"
    )
    print(f"Weekly contribution:  {plan.weekly_contribution:,.2f}")
    print(f"Buyable names:        {plan.buyable_count}")
    print(f"Blocked names:        {plan.blocked_count}")
    print(f"Planned investment:   {plan.planned_investment:,.2f}")
    print(f"Unallocated:          {plan.unallocated_contribution:,.2f}")
    print(f"Decision hash:        {plan.decision_hash}")
    print()
    for row in plan.decisions:
        print(
            f"{row.rank:>2}. {row.ticker:<6} "
            f"score={row.score:6.2f} "
            f"weight={row.pre_contribution_weight:7.2%} "
            f"{row.status:<7} "
            f"allocation={row.allocation_dollars:,.2f}"
        )
    print()
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")\n    run_log = args.run_log or (args.output_dir / "run_log.jsonl")\n    append_run_event(run_log, {"stage":"shadow_decision","status":"success","completed_at":utc_now_iso(),"decision_hash":plan.decision_hash,"decision_date":plan.decision_date.isoformat(),"planned_investment":plan.planned_investment,"inputs":{"long_growth":artifact_record(args.long_growth),"portfolio_state":artifact_record(args.portfolio_state) if args.portfolio_state else None},"outputs":{"json":artifact_record(json_path),"csv":artifact_record(csv_path)}})\n    print(f"Run log: {run_log}")


if __name__ == "__main__":
    main()
