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


THRESHOLDS = (None, 0.10, 0.125, 0.15)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test appreciation-only add-on thresholds for the frozen "
            "Top-10 long_growth_v1 portfolio."
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
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/appreciation_cap_v1"),
    )
    return parser.parse_args()


def strategy_id(threshold: float | None) -> str:
    if threshold is None:
        return "top10_uncapped"
    return "top10_addon_cap_" + str(threshold).replace(".", "_")


def run_one(
    frame: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    threshold: float | None,
    weekly_contribution: float,
    top_n: int,
    start: date,
    end: date,
):
    return run_ranked_accumulation_backtest(
        frame,
        price_store=store,
        model_id=strategy_id(threshold),
        score_column="long_growth_v1_score",
        config=BacktestConfig(
            weekly_contribution=weekly_contribution,
            top_n=top_n,
            selection_flag="top_conviction_eligible",
            max_position_weight=threshold,
        ),
        start=start,
        end=end,
    )


def main() -> None:
    args = parse_args()

    frame = pd.read_csv(args.long_growth, low_memory=False)
    store = BacktestPriceStore(args.prices)

    benchmark_store = None
    if args.benchmark_prices is not None:
        benchmark_store = BacktestPriceStore(
            args.benchmark_prices,
            ticker_column="ticker",
        )

    full_start = date(args.start_year, 1, 1)
    full_end = date(args.end_year, 12, 31)

    full_rows = []
    annual_rows = []
    trade_frames = []

    total = len(THRESHOLDS)

    for number, threshold in enumerate(THRESHOLDS, start=1):
        sid = strategy_id(threshold)
        label = "uncapped" if threshold is None else format(threshold, ".1%")

        print(
            "["
            + str(number)
            + "/"
            + str(total)
            + "] Running "
            + sid
            + " (add-on threshold "
            + label
            + ")...",
            flush=True,
        )

        result = run_one(
            frame,
            store=store,
            threshold=threshold,
            weekly_contribution=args.weekly_contribution,
            top_n=args.top_n,
            start=full_start,
            end=full_end,
        )

        row = dict(result.summary)
        row["strategy_id"] = sid
        row["addon_threshold"] = threshold
        full_rows.append(row)

        trades = result.trades.copy()
        trades["strategy_id"] = sid
        trade_frames.append(trades)

        print(
            "    full period: value="
            + format(result.summary["terminal_value"], ",.2f")
            + " XIRR="
            + format(result.summary["xirr"], ".2%")
            + " maxDD="
            + format(result.summary["max_drawdown"], ".2%")
            + " skips="
            + str(result.summary["position_cap_skip_count"])
            + " reentries="
            + str(result.summary["position_cap_reentry_count"])
            + " ending cash="
            + format(result.summary["ending_cash"], ",.2f"),
            flush=True,
        )

        for year in range(args.start_year, args.end_year + 1):
            print("    annual cohort " + str(year) + "...", flush=True)
            annual_result = run_one(
                frame,
                store=store,
                threshold=threshold,
                weekly_contribution=args.weekly_contribution,
                top_n=args.top_n,
                start=date(year, 1, 1),
                end=date(year, 12, 31),
            )
            annual_row = dict(annual_result.summary)
            annual_row["strategy_id"] = sid
            annual_row["addon_threshold"] = threshold
            annual_row["test_year"] = year
            annual_rows.append(annual_row)

    full = pd.DataFrame(full_rows)
    annual = pd.DataFrame(annual_rows)

    baseline = full.loc[full["addon_threshold"].isna()].iloc[0]

    comparison = full.copy()
    comparison["terminal_value_vs_uncapped"] = (
        comparison["terminal_value"] - baseline["terminal_value"]
    )
    comparison["xirr_vs_uncapped"] = (
        comparison["xirr"] - baseline["xirr"]
    )
    comparison["max_drawdown_vs_uncapped"] = (
        comparison["max_drawdown"] - baseline["max_drawdown"]
    )

    annual_baseline = annual.loc[
        annual["addon_threshold"].isna(),
        ["test_year", "terminal_value", "xirr", "max_drawdown"],
    ].rename(
        columns={
            "terminal_value": "baseline_terminal_value",
            "xirr": "baseline_xirr",
            "max_drawdown": "baseline_max_drawdown",
        }
    )

    annual_comparison = annual.merge(
        annual_baseline,
        on="test_year",
        how="left",
    )
    annual_comparison["terminal_value_vs_uncapped"] = (
        annual_comparison["terminal_value"]
        - annual_comparison["baseline_terminal_value"]
    )
    annual_comparison["xirr_vs_uncapped"] = (
        annual_comparison["xirr"]
        - annual_comparison["baseline_xirr"]
    )
    annual_comparison["max_drawdown_vs_uncapped"] = (
        annual_comparison["max_drawdown"]
        - annual_comparison["baseline_max_drawdown"]
    )

    aggregate_rows = []
    for sid, group in annual_comparison.groupby("strategy_id", sort=False):
        aggregate_rows.append({
            "strategy_id": sid,
            "addon_threshold": group["addon_threshold"].iloc[0],
            "years": len(group),
            "years_beating_uncapped": int(
                (group["terminal_value_vs_uncapped"] > 0).sum()
            ),
            "years_lower_drawdown_than_uncapped": int(
                (group["max_drawdown_vs_uncapped"] < 0).sum()
            ),
            "mean_terminal_value_vs_uncapped": group[
                "terminal_value_vs_uncapped"
            ].mean(),
            "mean_xirr_vs_uncapped": group[
                "xirr_vs_uncapped"
            ].mean(),
            "mean_drawdown_vs_uncapped": group[
                "max_drawdown_vs_uncapped"
            ].mean(),
            "mean_reentry_count": group[
                "position_cap_reentry_count"
            ].mean(),
            "mean_ending_cash_pct": group[
                "ending_cash_pct"
            ].mean(),
        })

    aggregate = pd.DataFrame(aggregate_rows)

    benchmark_summary = None
    if benchmark_store is not None:
        decision_dates = sorted(
            pd.to_datetime(
                frame.loc[
                    (
                        pd.to_datetime(
                            frame["decision_date"],
                            errors="coerce",
                        ).dt.date >= full_start
                    )
                    & (
                        pd.to_datetime(
                            frame["decision_date"],
                            errors="coerce",
                        ).dt.date <= full_end
                    ),
                    "decision_date",
                ],
                errors="coerce",
            ).dt.date.dropna().unique()
        )
        benchmark = run_single_asset_accumulation_backtest(
            price_store=benchmark_store,
            ticker=args.benchmark_symbol,
            decision_dates=decision_dates,
            weekly_contribution=args.weekly_contribution,
            model_id=args.benchmark_symbol.upper(),
        )
        benchmark_summary = pd.DataFrame([benchmark.summary])

    args.output_dir.mkdir(parents=True, exist_ok=True)

    full.to_csv(
        args.output_dir / "appreciation_cap_full_period.csv",
        index=False,
    )
    comparison.to_csv(
        args.output_dir / "appreciation_cap_comparison.csv",
        index=False,
    )
    annual.to_csv(
        args.output_dir / "appreciation_cap_annual.csv",
        index=False,
    )
    annual_comparison.to_csv(
        args.output_dir / "appreciation_cap_annual_comparison.csv",
        index=False,
    )
    aggregate.to_csv(
        args.output_dir / "appreciation_cap_annual_aggregate.csv",
        index=False,
    )
    pd.concat(trade_frames, ignore_index=True).to_csv(
        args.output_dir / "appreciation_cap_trades.csv",
        index=False,
    )

    if benchmark_summary is not None:
        benchmark_summary.to_csv(
            args.output_dir / "appreciation_cap_benchmark.csv",
            index=False,
        )

    print("Appreciation-only add-on threshold test complete.", flush=True)
    print("Reports: " + str(args.output_dir), flush=True)


if __name__ == "__main__":
    main()
