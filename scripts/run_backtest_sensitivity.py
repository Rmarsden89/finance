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


TOP_N_VALUES = (1, 3, 5, 10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run annual-cohort and top-N sensitivity backtests for "
            "long_growth_v1, full_growth_v1, and an optional benchmark."
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
    parser.add_argument("--selection-flag", default="top_conviction_eligible")
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--top-n",
        type=int,
        nargs="*",
        default=list(TOP_N_VALUES),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/backtest_sensitivity_v1"),
    )
    return parser.parse_args()


def run_model(
    *,
    frame: pd.DataFrame,
    price_store: BacktestPriceStore,
    model_id: str,
    score_column: str,
    top_n: int,
    weekly_contribution: float,
    selection_flag: str,
    start: date,
    end: date,
):
    return run_ranked_accumulation_backtest(
        frame,
        price_store=price_store,
        model_id=model_id,
        score_column=score_column,
        config=BacktestConfig(
            weekly_contribution=weekly_contribution,
            top_n=top_n,
            selection_flag=selection_flag,
        ),
        start=start,
        end=end,
    )


def main() -> None:
    args = parse_args()

    if args.weekly_contribution <= 0:
        raise ValueError("--weekly-contribution must be positive")
    if args.start_year > args.end_year:
        raise ValueError("--start-year must be <= --end-year")
    if not args.top_n or any(value <= 0 for value in args.top_n):
        raise ValueError("--top-n values must all be positive")

    long_frame = pd.read_csv(args.long_growth, low_memory=False)
    full_frame = pd.read_csv(args.full_growth, low_memory=False)
    price_store = BacktestPriceStore(args.prices)

    benchmark_store = None
    if args.benchmark_prices is not None:
        benchmark_store = BacktestPriceStore(
            args.benchmark_prices,
            ticker_column="ticker",
        )

    annual_rows = []
    annual_trade_frames = []

    for year in range(args.start_year, args.end_year + 1):
        start = date(year, 1, 1)
        end = date(year, 12, 31)

        for top_n in args.top_n:
            long_result = run_model(
                frame=long_frame,
                price_store=price_store,
                model_id="long_growth_v1",
                score_column="long_growth_v1_score",
                top_n=top_n,
                weekly_contribution=args.weekly_contribution,
                selection_flag=args.selection_flag,
                start=start,
                end=end,
            )
            full_result = run_model(
                frame=full_frame,
                price_store=price_store,
                model_id="full_growth_v1",
                score_column="full_growth_v1_score",
                top_n=top_n,
                weekly_contribution=args.weekly_contribution,
                selection_flag=args.selection_flag,
                start=start,
                end=end,
            )

            results = [long_result, full_result]

            if benchmark_store is not None:
                common_decisions = sorted(
                    set(long_result.weekly["decision_date"])
                    & set(full_result.weekly["decision_date"])
                )
                if common_decisions:
                    results.append(
                        run_single_asset_accumulation_backtest(
                            price_store=benchmark_store,
                            ticker=args.benchmark_symbol,
                            decision_dates=common_decisions,
                            weekly_contribution=args.weekly_contribution,
                            model_id=args.benchmark_symbol.upper(),
                        )
                    )

            for result in results:
                row = dict(result.summary)
                row["test_year"] = year
                row["top_n"] = top_n
                annual_rows.append(row)

                if not result.trades.empty:
                    trades = result.trades.copy()
                    trades["test_year"] = year
                    trades["top_n"] = top_n
                    annual_trade_frames.append(trades)

    annual = pd.DataFrame(annual_rows)

    pair_rows = []
    for (year, top_n), group in annual.groupby(
        ["test_year", "top_n"],
        sort=True,
    ):
        rows_by_model = {
            row.model_id: row
            for row in group.itertuples(index=False)
        }
        long_row = rows_by_model.get("long_growth_v1")
        full_row = rows_by_model.get("full_growth_v1")
        benchmark_row = rows_by_model.get(args.benchmark_symbol.upper())

        if long_row is None or full_row is None:
            continue

        long_terminal = float(long_row.terminal_value)
        full_terminal = float(full_row.terminal_value)

        if long_terminal > full_terminal:
            winner = "long_growth_v1"
        elif full_terminal > long_terminal:
            winner = "full_growth_v1"
        else:
            winner = "tie"

        pair = {
            "test_year": year,
            "top_n": top_n,
            "winner": winner,
            "long_terminal_value": long_terminal,
            "full_terminal_value": full_terminal,
            "terminal_value_delta_full_minus_long": (
                full_terminal - long_terminal
            ),
            "long_xirr": long_row.xirr,
            "full_xirr": full_row.xirr,
            "xirr_delta_full_minus_long": (
                full_row.xirr - long_row.xirr
            ),
            "long_max_drawdown": long_row.max_drawdown,
            "full_max_drawdown": full_row.max_drawdown,
            "max_drawdown_delta_full_minus_long": (
                full_row.max_drawdown - long_row.max_drawdown
            ),
        }

        if benchmark_row is not None:
            pair.update({
                "benchmark_terminal_value": benchmark_row.terminal_value,
                "long_terminal_vs_benchmark": (
                    long_terminal - benchmark_row.terminal_value
                ),
                "full_terminal_vs_benchmark": (
                    full_terminal - benchmark_row.terminal_value
                ),
                "benchmark_xirr": benchmark_row.xirr,
            })

        pair_rows.append(pair)

    pairwise = pd.DataFrame(pair_rows)

    sensitivity_rows = []
    for top_n, group in pairwise.groupby("top_n", sort=True):
        years = len(group)
        long_wins = int((group["winner"] == "long_growth_v1").sum())
        full_wins = int((group["winner"] == "full_growth_v1").sum())
        ties = int((group["winner"] == "tie").sum())

        sensitivity_rows.append({
            "top_n": top_n,
            "years": years,
            "long_wins": long_wins,
            "full_wins": full_wins,
            "ties": ties,
            "long_win_pct": long_wins / years if years else float("nan"),
            "full_win_pct": full_wins / years if years else float("nan"),
            "mean_terminal_delta_full_minus_long": group[
                "terminal_value_delta_full_minus_long"
            ].mean(),
            "median_terminal_delta_full_minus_long": group[
                "terminal_value_delta_full_minus_long"
            ].median(),
            "mean_xirr_delta_full_minus_long": group[
                "xirr_delta_full_minus_long"
            ].mean(),
            "median_xirr_delta_full_minus_long": group[
                "xirr_delta_full_minus_long"
            ].median(),
            "mean_drawdown_delta_full_minus_long": group[
                "max_drawdown_delta_full_minus_long"
            ].mean(),
        })

    sensitivity = pd.DataFrame(sensitivity_rows)

    full_period_rows = []
    full_start = date(args.start_year, 1, 1)
    full_end = date(args.end_year, 12, 31)

    for top_n in args.top_n:
        long_result = run_model(
            frame=long_frame,
            price_store=price_store,
            model_id="long_growth_v1",
            score_column="long_growth_v1_score",
            top_n=top_n,
            weekly_contribution=args.weekly_contribution,
            selection_flag=args.selection_flag,
            start=full_start,
            end=full_end,
        )
        full_result = run_model(
            frame=full_frame,
            price_store=price_store,
            model_id="full_growth_v1",
            score_column="full_growth_v1_score",
            top_n=top_n,
            weekly_contribution=args.weekly_contribution,
            selection_flag=args.selection_flag,
            start=full_start,
            end=full_end,
        )

        results = [long_result, full_result]

        if benchmark_store is not None:
            common_decisions = sorted(
                set(long_result.weekly["decision_date"])
                & set(full_result.weekly["decision_date"])
            )
            if common_decisions:
                results.append(
                    run_single_asset_accumulation_backtest(
                        price_store=benchmark_store,
                        ticker=args.benchmark_symbol,
                        decision_dates=common_decisions,
                        weekly_contribution=args.weekly_contribution,
                        model_id=args.benchmark_symbol.upper(),
                    )
                )

        for result in results:
            row = dict(result.summary)
            row["top_n"] = top_n
            full_period_rows.append(row)

    full_period = pd.DataFrame(full_period_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    annual.to_csv(
        args.output_dir / "annual_cohort_summary.csv",
        index=False,
    )
    pairwise.to_csv(
        args.output_dir / "annual_model_comparison.csv",
        index=False,
    )
    sensitivity.to_csv(
        args.output_dir / "topn_annual_sensitivity.csv",
        index=False,
    )
    full_period.to_csv(
        args.output_dir / "topn_full_period_summary.csv",
        index=False,
    )

    if annual_trade_frames:
        pd.concat(
            annual_trade_frames,
            ignore_index=True,
        ).to_csv(
            args.output_dir / "annual_cohort_trades.csv",
            index=False,
        )

    print("ANNUAL COHORT + TOP-N SENSITIVITY")
    print(
        "Years: "
        + str(args.start_year)
        + "-"
        + str(args.end_year)
        + " | Top-N: "
        + ", ".join(str(value) for value in args.top_n)
    )
    print()

    for row in sensitivity.itertuples(index=False):
        print(
            "Top "
            + str(int(row.top_n))
            + ": Long wins="
            + str(int(row.long_wins))
            + "/"
            + str(int(row.years))
            + " Full wins="
            + str(int(row.full_wins))
            + "/"
            + str(int(row.years))
            + " mean Full-Long terminal delta=USD "
            + format(row.mean_terminal_delta_full_minus_long, ",.2f")
        )

    print()
    print("Reports: " + str(args.output_dir))


if __name__ == "__main__":
    main()
