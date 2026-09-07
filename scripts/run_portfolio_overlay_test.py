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


TOP_N_VALUES = (3, 5, 10)
POSITION_CAPS = (None, 0.15, 0.20, 0.25)
STABILITY_FLOORS = (None, 20.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test portfolio-construction overlays around frozen long_growth_v1."
        )
    )
    parser.add_argument("--long-growth", type=Path, required=True)
    parser.add_argument("--family-scores", type=Path, required=True)
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument("--benchmark-prices", type=Path)
    parser.add_argument("--benchmark-symbol", default="VOO")
    parser.add_argument("--weekly-contribution", type=float, default=10.0)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/portfolio_overlay_v1"),
    )
    return parser.parse_args()


def strategy_id(
    *,
    top_n: int,
    cap: float | None,
    stability_floor: float | None,
) -> str:
    cap_text = "nocap" if cap is None else "cap" + str(int(cap * 100))
    stability_text = (
        "nostab"
        if stability_floor is None
        else "stab" + str(int(stability_floor))
    )
    return "top" + str(top_n) + "_" + cap_text + "_" + stability_text


def prepare_signals(
    long_growth: pd.DataFrame,
    family_scores: pd.DataFrame,
    *,
    stability_floor: float | None,
) -> pd.DataFrame:
    long = long_growth.copy()
    family = family_scores.copy()

    long["decision_date"] = pd.to_datetime(
        long["decision_date"],
        errors="coerce",
    )
    family["decision_date"] = pd.to_datetime(
        family["decision_date"],
        errors="coerce",
    )

    keys = ["decision_date", "ticker"]
    source = family[keys + ["stability_score"]].copy()

    merged = long.merge(
        source,
        on=keys,
        how="left",
        suffixes=("", "_family"),
    )

    base = merged["top_conviction_eligible"].fillna(False).astype(bool)
    if stability_floor is None:
        merged["overlay_eligible"] = base
    else:
        stability = pd.to_numeric(
            merged["stability_score"],
            errors="coerce",
        )
        merged["overlay_eligible"] = (
            base
            & stability.notna()
            & stability.ge(stability_floor)
        )

    return merged


def run_one(
    *,
    signals: pd.DataFrame,
    store: BacktestPriceStore,
    model_id: str,
    top_n: int,
    cap: float | None,
    weekly_contribution: float,
    start: date,
    end: date,
):
    return run_ranked_accumulation_backtest(
        signals,
        price_store=store,
        model_id=model_id,
        score_column="long_growth_v1_score",
        config=BacktestConfig(
            weekly_contribution=weekly_contribution,
            top_n=top_n,
            selection_flag="overlay_eligible",
            max_position_weight=cap,
        ),
        start=start,
        end=end,
    )


def main() -> None:
    args = parse_args()

    long_growth = pd.read_csv(args.long_growth, low_memory=False)
    family_scores = pd.read_csv(args.family_scores, low_memory=False)
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

    for stability_floor in STABILITY_FLOORS:
        signals = prepare_signals(
            long_growth,
            family_scores,
            stability_floor=stability_floor,
        )

        for top_n in TOP_N_VALUES:
            for cap in POSITION_CAPS:
                sid = strategy_id(
                    top_n=top_n,
                    cap=cap,
                    stability_floor=stability_floor,
                )

                full_result = run_one(
                    signals=signals,
                    store=store,
                    model_id=sid,
                    top_n=top_n,
                    cap=cap,
                    weekly_contribution=args.weekly_contribution,
                    start=full_start,
                    end=full_end,
                )

                row = dict(full_result.summary)
                row["strategy_id"] = sid
                row["stability_floor"] = stability_floor
                full_rows.append(row)

                if not full_result.trades.empty:
                    trades = full_result.trades.copy()
                    trades["strategy_id"] = sid
                    trades["test_scope"] = "full_period"
                    trade_frames.append(trades)

                for year in range(args.start_year, args.end_year + 1):
                    annual_result = run_one(
                        signals=signals,
                        store=store,
                        model_id=sid,
                        top_n=top_n,
                        cap=cap,
                        weekly_contribution=args.weekly_contribution,
                        start=date(year, 1, 1),
                        end=date(year, 12, 31),
                    )

                    annual_row = dict(annual_result.summary)
                    annual_row["strategy_id"] = sid
                    annual_row["stability_floor"] = stability_floor
                    annual_row["test_year"] = year
                    annual_rows.append(annual_row)

    full = pd.DataFrame(full_rows)
    annual = pd.DataFrame(annual_rows)

    benchmark_full = None
    benchmark_annual_rows = []

    if benchmark_store is not None:
        base_signals = prepare_signals(
            long_growth,
            family_scores,
            stability_floor=None,
        )
        base_dates = sorted(
            pd.to_datetime(
                base_signals.loc[
                    (
                        pd.to_datetime(
                            base_signals["decision_date"],
                            errors="coerce",
                        ).dt.date >= full_start
                    )
                    & (
                        pd.to_datetime(
                            base_signals["decision_date"],
                            errors="coerce",
                        ).dt.date <= full_end
                    ),
                    "decision_date",
                ],
                errors="coerce",
            ).dt.date.dropna().unique()
        )

        benchmark_full = run_single_asset_accumulation_backtest(
            price_store=benchmark_store,
            ticker=args.benchmark_symbol,
            decision_dates=base_dates,
            weekly_contribution=args.weekly_contribution,
            model_id=args.benchmark_symbol.upper(),
        )

        for year in range(args.start_year, args.end_year + 1):
            year_dates = [
                value
                for value in base_dates
                if value.year == year
            ]
            if not year_dates:
                continue
            result = run_single_asset_accumulation_backtest(
                price_store=benchmark_store,
                ticker=args.benchmark_symbol,
                decision_dates=year_dates,
                weekly_contribution=args.weekly_contribution,
                model_id=args.benchmark_symbol.upper(),
            )
            row = dict(result.summary)
            row["test_year"] = year
            benchmark_annual_rows.append(row)

    baseline = full.loc[
        (full["top_n"] == 5)
        & full["max_position_weight"].isna()
        & full["stability_floor"].isna()
    ]
    if baseline.empty:
        raise RuntimeError("Top-5 uncapped baseline was not produced")

    baseline_row = baseline.iloc[0]

    comparison = full.copy()
    comparison["terminal_value_vs_baseline"] = (
        comparison["terminal_value"] - baseline_row["terminal_value"]
    )
    comparison["xirr_vs_baseline"] = (
        comparison["xirr"] - baseline_row["xirr"]
    )
    comparison["max_drawdown_vs_baseline"] = (
        comparison["max_drawdown"] - baseline_row["max_drawdown"]
    )

    if benchmark_full is not None:
        comparison["terminal_value_vs_benchmark"] = (
            comparison["terminal_value"]
            - benchmark_full.summary["terminal_value"]
        )
        comparison["xirr_vs_benchmark"] = (
            comparison["xirr"] - benchmark_full.summary["xirr"]
        )
        comparison["max_drawdown_vs_benchmark"] = (
            comparison["max_drawdown"]
            - benchmark_full.summary["max_drawdown"]
        )

    annual_baseline = annual.loc[
        (annual["top_n"] == 5)
        & annual["max_position_weight"].isna()
        & annual["stability_floor"].isna()
    ][
        ["test_year", "terminal_value", "xirr", "max_drawdown"]
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
    annual_comparison["terminal_value_vs_baseline"] = (
        annual_comparison["terminal_value"]
        - annual_comparison["baseline_terminal_value"]
    )
    annual_comparison["xirr_vs_baseline"] = (
        annual_comparison["xirr"]
        - annual_comparison["baseline_xirr"]
    )
    annual_comparison["max_drawdown_vs_baseline"] = (
        annual_comparison["max_drawdown"]
        - annual_comparison["baseline_max_drawdown"]
    )

    if benchmark_annual_rows:
        benchmark_annual = pd.DataFrame(benchmark_annual_rows)[
            ["test_year", "terminal_value", "xirr", "max_drawdown"]
        ].rename(
            columns={
                "terminal_value": "benchmark_terminal_value",
                "xirr": "benchmark_xirr",
                "max_drawdown": "benchmark_max_drawdown",
            }
        )
        annual_comparison = annual_comparison.merge(
            benchmark_annual,
            on="test_year",
            how="left",
        )
        annual_comparison["terminal_value_vs_benchmark"] = (
            annual_comparison["terminal_value"]
            - annual_comparison["benchmark_terminal_value"]
        )

    aggregate_rows = []
    for sid, group in annual_comparison.groupby("strategy_id", sort=False):
        wins_baseline = int(
            (group["terminal_value_vs_baseline"] > 0).sum()
        )
        lower_drawdown = int(
            (group["max_drawdown_vs_baseline"] < 0).sum()
        )

        aggregate_rows.append({
            "strategy_id": sid,
            "top_n": int(group["top_n"].iloc[0]),
            "max_position_weight": group["max_position_weight"].iloc[0],
            "stability_floor": group["stability_floor"].iloc[0],
            "years": len(group),
            "years_beating_top5_baseline": wins_baseline,
            "years_lower_drawdown_than_top5_baseline": lower_drawdown,
            "mean_terminal_value_vs_baseline": group[
                "terminal_value_vs_baseline"
            ].mean(),
            "median_terminal_value_vs_baseline": group[
                "terminal_value_vs_baseline"
            ].median(),
            "mean_xirr_vs_baseline": group[
                "xirr_vs_baseline"
            ].mean(),
            "mean_drawdown_vs_baseline": group[
                "max_drawdown_vs_baseline"
            ].mean(),
        })

    aggregate = pd.DataFrame(aggregate_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    full.to_csv(
        args.output_dir / "overlay_full_period_summary.csv",
        index=False,
    )
    comparison.to_csv(
        args.output_dir / "overlay_full_period_comparison.csv",
        index=False,
    )
    annual.to_csv(
        args.output_dir / "overlay_annual_summary.csv",
        index=False,
    )
    annual_comparison.to_csv(
        args.output_dir / "overlay_annual_comparison.csv",
        index=False,
    )
    aggregate.to_csv(
        args.output_dir / "overlay_annual_aggregate.csv",
        index=False,
    )

    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(
            args.output_dir / "overlay_full_period_trades.csv",
            index=False,
        )

    if benchmark_full is not None:
        pd.DataFrame([benchmark_full.summary]).to_csv(
            args.output_dir / "overlay_benchmark_summary.csv",
            index=False,
        )

    print("LONG_GROWTH_V1 PORTFOLIO OVERLAY TEST")
    print(
        "Configurations: "
        + str(len(full))
        + " | annual cohorts: "
        + str(len(annual))
    )
    print(
        "Baseline terminal value: "
        + format(float(baseline_row["terminal_value"]), ",.2f")
    )
    print(
        "Baseline XIRR: "
        + format(float(baseline_row["xirr"]), ".2%")
    )
    print(
        "Baseline max drawdown: "
        + format(float(baseline_row["max_drawdown"]), ".2%")
    )
    print()
    print("Reports: " + str(args.output_dir))


if __name__ == "__main__":
    main()
