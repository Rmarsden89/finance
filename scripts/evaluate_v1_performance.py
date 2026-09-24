from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.evaluation import EvaluationError, evaluate_v1_live_performance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate completed long_growth_v1 live runs against a synthetic "
            "matched-cash-flow SPY benchmark. This command has no brokerage "
            "or order capability and does not modify V1."
        )
    )
    parser.add_argument("--shadow-root", type=Path, default=Path("reports/shadow"))
    parser.add_argument(
        "--benchmark-prices",
        type=Path,
        default=Path("data/market/benchmark_spy.csv"),
    )
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/v1_evaluation"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = evaluate_v1_live_performance(
            shadow_root=args.shadow_root,
            benchmark_prices_path=args.benchmark_prices,
            benchmark=args.benchmark,
        )
    except EvaluationError as exc:
        raise SystemExit(f"V1 evaluation failed closed: {exc}") from exc

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_json = args.output_dir / "performance_summary.json"
    summary_csv = args.output_dir / "performance_summary.csv"
    weekly_csv = args.output_dir / "weekly_portfolio_history.csv"
    benchmark_csv = args.output_dir / "benchmark_history.csv"
    cohorts_csv = args.output_dir / "selection_cohorts.csv"
    diagnostics_csv = args.output_dir / "selection_diagnostics.csv"
    forwards_csv = args.output_dir / "selection_forward_returns.csv"

    summary_json.write_text(
        json.dumps(result.summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame([result.summary]).to_csv(summary_csv, index=False)
    result.weekly_history.to_csv(weekly_csv, index=False)
    result.benchmark_history.to_csv(benchmark_csv, index=False)
    result.selection_cohorts.to_csv(cohorts_csv, index=False)
    result.selection_diagnostics.to_csv(diagnostics_csv, index=False)
    result.selection_forward_returns.to_csv(forwards_csv, index=False)

    s = result.summary
    print("V1 PERFORMANCE EVALUATION")
    print("Evaluation only:          YES")
    print("Broker/order capability:  NONE")
    print(f"Benchmark:                {s['benchmark']} matched cash flow")
    print(f"Completed live runs:      {s['completed_runs']}")
    print(f"Contributed/deployed:     ${s['cumulative_contributed']:.2f}")
    print(f"V1 position value:        ${s['v1_position_value']:.2f}")
    print(f"Strategy cash value:      ${s['strategy_cash_value']:.2f}")
    print(f"V1 sleeve value:          ${s['v1_sleeve_value']:.2f}")
    print(f"V1 P/L:                   ${s['v1_profit_loss']:+.2f}")
    print(f"V1 deployed-cap return:   {s['v1_deployed_capital_return']:+.2%}")
    print(f"{s['benchmark']} value:                 ${s['benchmark_value']:.2f}")
    print(f"{s['benchmark']} P/L:                   ${s['benchmark_profit_loss']:+.2f}")
    print(f"{s['benchmark']} deployed-cap return: {s['benchmark_deployed_capital_return']:+.2%}")
    print(f"Excess value:             ${s['excess_value']:+.2f}")
    print(f"Excess deployed-cap:      {s['excess_deployed_capital_return']:+.2%}")
    print(f"Cash accounting:          {s['cash_accounting_status']}")
    print(f"Distinct selections:      {s['distinct_selected_tickers']}")
    print(f"Selection events:         {s['selection_events']}")
    print("Repeated selections:      " + ", ".join(s["repeated_selection_tickers"]))
    print("One-off selections:       " + ", ".join(s["one_off_selection_tickers"]))
    print("Latest new entries:       " + (", ".join(s["latest_new_entry_tickers"]) or "none"))
    print("Latest dropped:           " + (", ".join(s["latest_dropped_tickers"]) or "none"))
    if s["latest_selection_retention_rate"] is not None:
        print(f"Latest retention:         {s['latest_selection_retention_rate']:.2%}")
    print(f"Current positions:        {s['current_position_count']}")
    print()
    print(f"Summary:                  {summary_json}")
    print(f"Weekly history:           {weekly_csv}")
    print(f"Benchmark history:        {benchmark_csv}")
    print(f"Selection cohorts:        {cohorts_csv}")
    print(f"Selection diagnostics:    {diagnostics_csv}")
    print(f"Forward returns:          {forwards_csv}")


if __name__ == "__main__":
    main()
