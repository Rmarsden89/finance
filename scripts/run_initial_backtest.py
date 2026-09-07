from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from finance.backtest import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
    run_single_asset_accumulation_backtest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the first weekly accumulation backtest for long_growth_v1 "
            "and full_growth_v1 under identical portfolio rules."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument("--full-growth", type=Path, required=True)
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument("--benchmark-prices", type=Path)
    parser.add_argument("--benchmark-symbol", default="VOO")
    parser.add_argument("--weekly-contribution", type=float, default=10.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--selection-flag", default="top_conviction_eligible")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2016, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/backtest_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.weekly_contribution <= 0:
        raise ValueError("--weekly-contribution must be positive")
    if args.top_n <= 0:
        raise ValueError("--top-n must be positive")

    price_store = BacktestPriceStore(args.prices)
    config = BacktestConfig(
        weekly_contribution=args.weekly_contribution,
        top_n=args.top_n,
        selection_flag=args.selection_flag,
    )

    long_frame = pd.read_csv(args.long_growth, low_memory=False)
    full_frame = pd.read_csv(args.full_growth, low_memory=False)

    long_result = run_ranked_accumulation_backtest(
        long_frame,
        price_store=price_store,
        model_id="long_growth_v1",
        score_column="long_growth_v1_score",
        config=config,
        start=args.start,
        end=args.end,
    )
    full_result = run_ranked_accumulation_backtest(
        full_frame,
        price_store=price_store,
        model_id="full_growth_v1",
        score_column="full_growth_v1_score",
        config=config,
        start=args.start,
        end=args.end,
    )

    results = [long_result, full_result]

    if args.benchmark_prices is not None:
        benchmark_store = BacktestPriceStore(
            args.benchmark_prices,
            ticker_column="ticker",
        )
        common_decisions = sorted(
            set(long_result.weekly["decision_date"])
            & set(full_result.weekly["decision_date"])
        )
        benchmark_result = run_single_asset_accumulation_backtest(
            price_store=benchmark_store,
            ticker=args.benchmark_symbol,
            decision_dates=common_decisions,
            weekly_contribution=args.weekly_contribution,
            model_id=args.benchmark_symbol.upper(),
        )
        results.append(benchmark_result)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    summary = pd.DataFrame([result.summary for result in results])
    summary.to_csv(args.output_dir / "backtest_summary.csv", index=False)

    weekly = pd.concat([result.weekly for result in results], ignore_index=True)
    weekly.to_csv(args.output_dir / "backtest_weekly.csv", index=False)

    trades = pd.concat([result.trades for result in results], ignore_index=True)
    trades.to_csv(args.output_dir / "backtest_trades.csv", index=False)

    yearly_rows = []
    for model_id, group in weekly.groupby("model_id", sort=False):
        group = group.copy()
        group["valuation_date"] = pd.to_datetime(
            group["valuation_date"],
            errors="coerce",
        )
        group["year"] = group["valuation_date"].dt.year
        for year, year_group in group.groupby("year", dropna=True):
            final = year_group.sort_values("valuation_date").iloc[-1]
            yearly_rows.append({
                "model_id": model_id,
                "year": int(year),
                "total_contributed": final["total_contributed"],
                "portfolio_value": final["portfolio_value"],
                "gain_dollars": final["gain_dollars"],
                "gain_on_contributions_pct": final["gain_on_contributions_pct"],
                "holding_count": final["holding_count"],
            })
    pd.DataFrame(yearly_rows).to_csv(
        args.output_dir / "backtest_yearly.csv",
        index=False,
    )

    print("INITIAL ACCUMULATION BACKTEST")
    print(
        f"Rules: ${args.weekly_contribution:.2f}/week, "
        f"top {args.top_n}, selection={args.selection_flag}"
    )
    print()
    for row in summary.itertuples(index=False):
        xirr = getattr(row, "xirr")
        xirr_text = f"{xirr:.2%}" if pd.notna(xirr) else "n/a"
        print(
            f"{row.model_id:20s} "
            f"contributed=${row.total_contributed:,.2f} "
            f"value=${row.terminal_value:,.2f} "
            f"gain=${row.gain_dollars:,.2f} "
            f"XIRR={xirr_text}"
        )

    if args.benchmark_prices is None:
        print()
        print(
            "Benchmark not run: provide --benchmark-prices after fetching "
            f"{args.benchmark_symbol.upper()}."
        )

    print()
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
