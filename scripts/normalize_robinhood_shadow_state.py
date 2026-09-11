from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from finance.shadow.robinhood_state import (
    load_robinhood_shadow_export,
    normalize_robinhood_shadow_state,
)
from finance.shadow.run_log import artifact_record, append_run_event, utc_now_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a read-only Robinhood shadow-state export."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--run-log", type=Path)
    parser.add_argument(
        "--portfolio-output",
        type=Path,
        default=Path("reports/shadow/current/portfolio_state.csv"),
    )
    parser.add_argument(
        "--orders-output",
        type=Path,
        default=Path("reports/shadow/current/non_final_orders.csv"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = load_robinhood_shadow_export(args.input)
    positions, orders, audit = normalize_robinhood_shadow_state(payload)

    if not positions.empty and positions["market_value"].isna().any():
        missing = ", ".join(
            positions.loc[positions["market_value"].isna(), "ticker"].tolist()
        )
        raise ValueError(f"Missing derived market value for broker positions: {missing}")

    args.portfolio_output.parent.mkdir(parents=True, exist_ok=True)
    args.orders_output.parent.mkdir(parents=True, exist_ok=True)

    portfolio_state = (
        positions[["ticker", "market_value"]]
        if not positions.empty
        else pd.DataFrame(columns=["ticker", "market_value"])
    )
    portfolio_state.to_csv(args.portfolio_output, index=False)
    orders.to_csv(args.orders_output, index=False)

    print("ROBINHOOD SHADOW STATE")
    print(f"Account value:           ${audit.account_value:,.2f}")
    print(f"Equity value:            ${audit.equity_value:,.2f}")
    print(f"Cash:                    ${audit.cash:,.2f}")
    print(f"Buying power:            ${audit.buying_power:,.2f}")
    print(f"Positions:               {audit.positions}")
    print(f"Valued positions:        {audit.valued_positions}")
    print(f"Non-final equity orders: {audit.non_final_equity_orders}")
    print(f"Top-10 tradable:         {audit.top10_tradable}/{audit.top10_checked}")
    print(f"Portfolio CSV:           {args.portfolio_output}")
    print(f"Orders CSV:              {args.orders_output}")

    run_log = args.run_log or (args.portfolio_output.parent / "run_log.jsonl")
    append_run_event(
        run_log,
        {
            "stage": "broker_state_normalize",
            "status": "success",
            "completed_at": utc_now_iso(),
            "account_value": audit.account_value,
            "buying_power": audit.buying_power,
            "positions": audit.positions,
            "non_final_equity_orders": audit.non_final_equity_orders,
            "inputs": {
                "broker_export": artifact_record(args.input),
            },
            "outputs": {
                "portfolio_state": artifact_record(args.portfolio_output),
                "non_final_orders": artifact_record(args.orders_output),
            },
        },
    )
    print(f"Run log:                 {run_log}")


if __name__ == "__main__":
    main()
