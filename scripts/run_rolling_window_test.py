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


WINDOW_LENGTHS = (3, 5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run rolling 3-year and 5-year robustness tests for the frozen "
            "Top-10 long_growth_v1 portfolio with and without the 10% "
            "appreciation-only add-on threshold."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument("--benchmark-prices", type=Path)
    parser.add_argument("--benchmark-symbol", default="VOO")
    parser.add_argument("--weekly-contribution", type=float, default=10.0)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument(
        "--addon-threshold",
        type=float,
        default=0.10,
    )
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/rolling_window_v1"),
    )
    return parser.parse_args()


def run_strategy(
    frame: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    model_id: str,
    top_n: int,
    weekly_contribution: float,
    addon_threshold: float | None,
    start: date,
    end: date,
):
    return run_ranked_accumulation_backtest(
        frame,
        price_store=store,
        model_id=model_id,
        score_column="long_growth_v1_score",
        config=BacktestConfig(
            weekly_contribution=weekly_contribution,
            top_n=top_n,
            selection_flag="top_conviction_eligible",
            max_addon_position_weight=addon_threshold,
        ),
        start=start,
        end=end,
    )


def main() -> None:
    args = parse_args()

    if not 0 < args.addon_threshold <= 1:
        raise ValueError("--addon-threshold must be in (0, 1]")

    frame = pd.read_csv(args.long_growth, low_memory=False)
    store = BacktestPriceStore(args.prices)

    benchmark_store = None
    if args.benchmark_prices is not None:
        benchmark_store = BacktestPriceStore(
            args.benchmark_prices,
            ticker_column="ticker",
        )

    rows = []
    total_windows = sum(
        max(0, args.end_year - args.start_year - length + 2)
        for length in WINDOW_LENGTHS
    )
    window_number = 0

    print(
        "Starting rolling-window robustness test: "
        + str(total_windows)
        + " windows",
        flush=True,
    )

    for length in WINDOW_LENGTHS:
        final_start_year = args.end_year - length + 1

        for start_year in range(args.start_year, final_start_year + 1):
            end_year = start_year + length - 1
            window_number += 1

            start = date(start_year, 1, 1)
            end = date(end_year, 12, 31)

            print(
                "["
                + str(window_number)
                + "/"
                + str(total_windows)
                + "] "
                + str(length)
                + "-year window "
                + str(start_year)
                + "-"
                + str(end_year)
                + "...",
                flush=True,
            )

            uncapped = run_strategy(
                frame,
                store=store,
                model_id="long_growth_top10_uncapped",
                top_n=args.top_n,
                weekly_contribution=args.weekly_contribution,
                addon_threshold=None,
                start=start,
                end=end,
            )

            capped = run_strategy(
                frame,
                store=store,
                model_id="long_growth_top10_addon_cap",
                top_n=args.top_n,
                weekly_contribution=args.weekly_contribution,
                addon_threshold=args.addon_threshold,
                start=start,
                end=end,
            )

            result_set = [uncapped, capped]

            benchmark = None
            if benchmark_store is not None:
                decision_dates = sorted(
                    set(uncapped.weekly["decision_date"])
                    & set(capped.weekly["decision_date"])
                )
                if decision_dates:
                    benchmark = run_single_asset_accumulation_backtest(
                        price_store=benchmark_store,
                        ticker=args.benchmark_symbol,
                        decision_dates=decision_dates,
                        weekly_contribution=args.weekly_contribution,
                        model_id=args.benchmark_symbol.upper(),
                    )
                    result_set.append(benchmark)

            for result in result_set:
                row = dict(result.summary)
                row["window_years"] = length
                row["window_start_year"] = start_year
                row["window_end_year"] = end_year
                rows.append(row)

            print(
                "    uncapped value="
                + format(uncapped.summary["terminal_value"], ",.2f")
                + " XIRR="
                + format(uncapped.summary["xirr"], ".2%")
                + " | capped value="
                + format(capped.summary["terminal_value"], ",.2f")
                + " XIRR="
                + format(capped.summary["xirr"], ".2%")
                + " reentries="
                + str(capped.summary["position_cap_reentry_count"]),
                flush=True,
            )

    results = pd.DataFrame(rows)

    comparison_rows = []

    for (length, start_year, end_year), group in results.groupby(
        ["window_years", "window_start_year", "window_end_year"],
        sort=True,
    ):
        by_model = {
            row.model_id: row
            for row in group.itertuples(index=False)
        }

        uncapped = by_model.get("long_growth_top10_uncapped")
        capped = by_model.get("long_growth_top10_addon_cap")
        benchmark = by_model.get(args.benchmark_symbol.upper())

        if uncapped is None or capped is None:
            continue

        row = {
            "window_years": length,
            "window_start_year": start_year,
            "window_end_year": end_year,
            "uncapped_terminal_value": uncapped.terminal_value,
            "capped_terminal_value": capped.terminal_value,
            "terminal_delta_capped_minus_uncapped": (
                capped.terminal_value - uncapped.terminal_value
            ),
            "uncapped_xirr": uncapped.xirr,
            "capped_xirr": capped.xirr,
            "xirr_delta_capped_minus_uncapped": (
                capped.xirr - uncapped.xirr
            ),
            "uncapped_max_drawdown": uncapped.max_drawdown,
            "capped_max_drawdown": capped.max_drawdown,
            "max_drawdown_delta_capped_minus_uncapped": (
                capped.max_drawdown - uncapped.max_drawdown
            ),
            "capped_reentry_count": capped.position_cap_reentry_count,
            "capped_skip_count": capped.position_cap_skip_count,
            "capped_ending_cash_pct": capped.ending_cash_pct,
        }

        if benchmark is not None:
            row.update({
                "benchmark_terminal_value": benchmark.terminal_value,
                "benchmark_xirr": benchmark.xirr,
                "benchmark_max_drawdown": benchmark.max_drawdown,
                "uncapped_terminal_vs_benchmark": (
                    uncapped.terminal_value - benchmark.terminal_value
                ),
                "capped_terminal_vs_benchmark": (
                    capped.terminal_value - benchmark.terminal_value
                ),
                "uncapped_xirr_vs_benchmark": (
                    uncapped.xirr - benchmark.xirr
                ),
                "capped_xirr_vs_benchmark": (
                    capped.xirr - benchmark.xirr
                ),
            })

        comparison_rows.append(row)

    comparison = pd.DataFrame(comparison_rows)

    aggregate_rows = []

    for length, group in comparison.groupby("window_years", sort=True):
        row = {
            "window_years": length,
            "windows": len(group),
            "capped_beats_uncapped_terminal_count": int(
                (group["terminal_delta_capped_minus_uncapped"] > 0).sum()
            ),
            "capped_beats_uncapped_xirr_count": int(
                (group["xirr_delta_capped_minus_uncapped"] > 0).sum()
            ),
            "capped_lower_drawdown_count": int(
                (
                    group[
                        "max_drawdown_delta_capped_minus_uncapped"
                    ] < 0
                ).sum()
            ),
            "mean_terminal_delta_capped_minus_uncapped": group[
                "terminal_delta_capped_minus_uncapped"
            ].mean(),
            "median_terminal_delta_capped_minus_uncapped": group[
                "terminal_delta_capped_minus_uncapped"
            ].median(),
            "mean_xirr_delta_capped_minus_uncapped": group[
                "xirr_delta_capped_minus_uncapped"
            ].mean(),
            "median_xirr_delta_capped_minus_uncapped": group[
                "xirr_delta_capped_minus_uncapped"
            ].median(),
            "mean_drawdown_delta_capped_minus_uncapped": group[
                "max_drawdown_delta_capped_minus_uncapped"
            ].mean(),
            "mean_capped_reentry_count": group[
                "capped_reentry_count"
            ].mean(),
            "mean_capped_ending_cash_pct": group[
                "capped_ending_cash_pct"
            ].mean(),
        }

        if "benchmark_terminal_value" in group.columns:
            row.update({
                "uncapped_beats_benchmark_terminal_count": int(
                    (group["uncapped_terminal_vs_benchmark"] > 0).sum()
                ),
                "capped_beats_benchmark_terminal_count": int(
                    (group["capped_terminal_vs_benchmark"] > 0).sum()
                ),
                "uncapped_beats_benchmark_xirr_count": int(
                    (group["uncapped_xirr_vs_benchmark"] > 0).sum()
                ),
                "capped_beats_benchmark_xirr_count": int(
                    (group["capped_xirr_vs_benchmark"] > 0).sum()
                ),
                "mean_uncapped_xirr_vs_benchmark": group[
                    "uncapped_xirr_vs_benchmark"
                ].mean(),
                "mean_capped_xirr_vs_benchmark": group[
                    "capped_xirr_vs_benchmark"
                ].mean(),
            })

        aggregate_rows.append(row)

    aggregate = pd.DataFrame(aggregate_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    results.to_csv(
        args.output_dir / "rolling_window_summary.csv",
        index=False,
    )
    comparison.to_csv(
        args.output_dir / "rolling_window_comparison.csv",
        index=False,
    )
    aggregate.to_csv(
        args.output_dir / "rolling_window_aggregate.csv",
        index=False,
    )

    print("Rolling-window robustness test complete.", flush=True)
    print("Reports: " + str(args.output_dir), flush=True)


if __name__ == "__main__":
    main()
