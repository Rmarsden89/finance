from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finance.backtest import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
    run_single_asset_accumulation_backtest,
)
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_promotion_criteria import (
    ROLLING_WINDOW_YEARS,
    TTM_CHALLENGER,
    TTM_PROMOTION_THRESHOLDS,
)
from finance.research.v2 import resolve_v2_ttm_diagnostic_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the frozen V2 TTM challenger against V1 using the "
            "predeclared historical promotion gates. Research-only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument(
        "--benchmark-prices",
        type=Path,
        default=Path("data/market/benchmark_voo.csv"),
    )
    parser.add_argument("--benchmark-symbol", default="VOO")
    parser.add_argument("--end-year", type=int, default=2025)
    return parser.parse_args()


def _top10_sets(
    frame: pd.DataFrame,
    *,
    score_column: str,
    eligible_column: str,
) -> dict[date, set[str]]:
    data = frame.copy()
    data["decision_date"] = pd.to_datetime(
        data["decision_date"], errors="raise"
    ).dt.date
    data[score_column] = pd.to_numeric(data[score_column], errors="coerce")
    result: dict[date, set[str]] = {}
    for decision_date, group in data.groupby("decision_date", sort=True):
        selected = group.loc[
            group[eligible_column].fillna(False).astype(bool)
            & group[score_column].notna()
        ].sort_values(
            [score_column, "ticker"],
            ascending=[False, True],
            kind="stable",
        ).head(TTM_CHALLENGER.top_n)
        result[decision_date] = set(selected["ticker"].astype(str).str.upper())
    return result


def _turnover_summary(
    v1: pd.DataFrame,
    v2: pd.DataFrame,
) -> pd.DataFrame:
    variants = {
        "v1": (
            v1,
            "long_growth_v1_score",
            "top_conviction_eligible",
        ),
        "v2": (
            v2,
            f"{TTM_CHALLENGER.model_id}_score",
            "v2_top_conviction_eligible",
        ),
    }
    rows = []
    for name, (frame, score_column, eligible_column) in variants.items():
        sets = _top10_sets(
            frame,
            score_column=score_column,
            eligible_column=eligible_column,
        )
        prior = None
        replacements = []
        for decision_date in sorted(sets):
            current = sets[decision_date]
            if (
                prior is not None
                and len(prior) == TTM_CHALLENGER.top_n
                and len(current) == TTM_CHALLENGER.top_n
            ):
                replacements.append(len(current - prior))
            prior = current
        rows.append({
            "variant": name,
            "valid_transitions": len(replacements),
            "mean_replacements": (
                float(np.mean(replacements)) if replacements else np.nan
            ),
            "median_replacements": (
                float(np.median(replacements)) if replacements else np.nan
            ),
            "mean_replacement_rate": (
                float(np.mean(replacements) / TTM_CHALLENGER.top_n)
                if replacements else np.nan
            ),
        })
    return pd.DataFrame(rows)


def _concentration_summary(
    v1: pd.DataFrame,
    v2: pd.DataFrame,
) -> pd.DataFrame:
    variants = {
        "v1": (
            v1,
            "long_growth_v1_score",
            "top_conviction_eligible",
        ),
        "v2": (
            v2,
            f"{TTM_CHALLENGER.model_id}_score",
            "v2_top_conviction_eligible",
        ),
    }
    rows = []
    for name, (frame, score_column, eligible_column) in variants.items():
        data = frame.copy()
        data["decision_date"] = pd.to_datetime(
            data["decision_date"], errors="raise"
        ).dt.date
        data[score_column] = pd.to_numeric(data[score_column], errors="coerce")
        data["market_cap"] = pd.to_numeric(data.get("market_cap"), errors="coerce")

        weekly_hhi = []
        weekly_top1 = []
        valid_weeks = 0
        for _, group in data.groupby("decision_date", sort=True):
            selected = group.loc[
                group[eligible_column].fillna(False).astype(bool)
                & group[score_column].notna()
            ].sort_values(
                [score_column, "ticker"],
                ascending=[False, True],
                kind="stable",
            ).head(TTM_CHALLENGER.top_n)
            caps = selected["market_cap"].dropna()
            caps = caps.loc[caps.gt(0)]
            if len(caps) != TTM_CHALLENGER.top_n:
                continue
            weights = caps / caps.sum()
            weekly_hhi.append(float((weights ** 2).sum()))
            weekly_top1.append(float(weights.max()))
            valid_weeks += 1

        rows.append({
            "variant": name,
            "valid_weeks": valid_weeks,
            "mean_top10_market_cap_hhi": (
                float(np.mean(weekly_hhi)) if weekly_hhi else np.nan
            ),
            "median_top10_market_cap_hhi": (
                float(np.median(weekly_hhi)) if weekly_hhi else np.nan
            ),
            "mean_largest_market_cap_share": (
                float(np.mean(weekly_top1)) if weekly_top1 else np.nan
            ),
            "median_largest_market_cap_share": (
                float(np.median(weekly_top1)) if weekly_top1 else np.nan
            ),
        })
    return pd.DataFrame(rows)


def _run_model(
    frame: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    model_id: str,
    score_column: str,
    eligible_column: str,
    start: date,
    end: date | None,
):
    return run_ranked_accumulation_backtest(
        frame,
        price_store=store,
        model_id=model_id,
        score_column=score_column,
        config=BacktestConfig(
            weekly_contribution=TTM_CHALLENGER.weekly_contribution,
            top_n=TTM_CHALLENGER.top_n,
            selection_flag=eligible_column,
            max_addon_position_weight=TTM_CHALLENGER.max_addon_position_weight,
        ),
        start=start,
        end=end,
    )


def _validate_benchmark_result(
    result,
    *,
    expected_decision_weeks: int,
    benchmark_symbol: str,
    scope: str,
) -> None:
    """Fail closed when a benchmark run did not actually invest."""

    summary = result.summary
    buy_count = int(summary.get("buy_count", 0))
    decision_weeks = int(summary.get("decision_weeks", 0))
    unfilled = int(summary.get("unfilled_order_count", 0))

    if decision_weeks != expected_decision_weeks:
        raise SystemExit(
            f"{scope} {benchmark_symbol} benchmark decision-week mismatch: "
            f"{decision_weeks} != {expected_decision_weeks}"
        )
    if buy_count == 0:
        raise SystemExit(
            f"{scope} {benchmark_symbol} benchmark executed zero buys. "
            "Check benchmark file ticker, date coverage, and price columns."
        )
    if buy_count + unfilled < expected_decision_weeks:
        raise SystemExit(
            f"{scope} {benchmark_symbol} benchmark did not account for every "
            f"decision week: buys={buy_count}, unfilled={unfilled}, "
            f"expected={expected_decision_weeks}"
        )


def _rolling(
    *,
    v1: pd.DataFrame,
    v2: pd.DataFrame,
    price_store: BacktestPriceStore,
    benchmark_store: BacktestPriceStore,
    benchmark_symbol: str,
    end_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for window_years in ROLLING_WINDOW_YEARS:
        final_start = end_year - window_years + 1
        for start_year in range(
            TTM_CHALLENGER.evaluation_start.year,
            final_start + 1,
        ):
            end_window_year = start_year + window_years - 1
            start = date(start_year, 1, 1)
            end = date(end_window_year, 12, 31)

            v1_result = _run_model(
                v1,
                store=price_store,
                model_id="long_growth_v1",
                score_column="long_growth_v1_score",
                eligible_column="top_conviction_eligible",
                start=start,
                end=end,
            )
            v2_result = _run_model(
                v2,
                store=price_store,
                model_id=TTM_CHALLENGER.model_id,
                score_column=f"{TTM_CHALLENGER.model_id}_score",
                eligible_column="v2_top_conviction_eligible",
                start=start,
                end=end,
            )

            common_dates = sorted(
                set(v1_result.weekly["decision_date"])
                & set(v2_result.weekly["decision_date"])
            )
            benchmark = run_single_asset_accumulation_backtest(
                price_store=benchmark_store,
                ticker=benchmark_symbol,
                decision_dates=common_dates,
                weekly_contribution=TTM_CHALLENGER.weekly_contribution,
                model_id=benchmark_symbol.upper(),
            )
            _validate_benchmark_result(
                benchmark,
                expected_decision_weeks=len(common_dates),
                benchmark_symbol=benchmark_symbol.upper(),
                scope=f"{window_years}y {start_year}-{end_window_year}",
            )

            rows.append({
                "window_years": window_years,
                "window_start_year": start_year,
                "window_end_year": end_window_year,
                "v1_xirr": v1_result.summary["xirr"],
                "v2_xirr": v2_result.summary["xirr"],
                "xirr_delta_v2_minus_v1": (
                    v2_result.summary["xirr"] - v1_result.summary["xirr"]
                ),
                "v1_max_drawdown": v1_result.summary["max_drawdown"],
                "v2_max_drawdown": v2_result.summary["max_drawdown"],
                "drawdown_delta_v2_minus_v1": (
                    v2_result.summary["max_drawdown"]
                    - v1_result.summary["max_drawdown"]
                ),
                "benchmark_xirr": benchmark.summary["xirr"],
                "v1_xirr_vs_benchmark": (
                    v1_result.summary["xirr"] - benchmark.summary["xirr"]
                ),
                "v2_xirr_vs_benchmark": (
                    v2_result.summary["xirr"] - benchmark.summary["xirr"]
                ),
                "v1_terminal_value": v1_result.summary["terminal_value"],
                "v2_terminal_value": v2_result.summary["terminal_value"],
                "benchmark_terminal_value": benchmark.summary["terminal_value"],
            })

    detail = pd.DataFrame(rows)
    aggregate_rows = []
    for window_years, group in detail.groupby("window_years", sort=True):
        aggregate_rows.append({
            "window_years": int(window_years),
            "windows": len(group),
            "v2_xirr_win_count": int(
                group["xirr_delta_v2_minus_v1"].ge(0).sum()
            ),
            "v2_xirr_win_rate": float(
                group["xirr_delta_v2_minus_v1"].ge(0).mean()
            ),
            "median_xirr_delta_v2_minus_v1": float(
                group["xirr_delta_v2_minus_v1"].median()
            ),
            "mean_xirr_delta_v2_minus_v1": float(
                group["xirr_delta_v2_minus_v1"].mean()
            ),
            "v1_beats_benchmark_count": int(
                group["v1_xirr_vs_benchmark"].gt(0).sum()
            ),
            "v2_beats_benchmark_count": int(
                group["v2_xirr_vs_benchmark"].gt(0).sum()
            ),
            "benchmark_beating_window_count_delta": int(
                group["v2_xirr_vs_benchmark"].gt(0).sum()
                - group["v1_xirr_vs_benchmark"].gt(0).sum()
            ),
            "mean_drawdown_delta_v2_minus_v1": float(
                group["drawdown_delta_v2_minus_v1"].mean()
            ),
        })
    return detail, pd.DataFrame(aggregate_rows)


def _gate(
    rows: list[dict[str, object]],
    *,
    name: str,
    actual: object,
    threshold: object,
    passed: bool,
    scope: str,
) -> None:
    rows.append({
        "gate": name,
        "scope": scope,
        "actual": actual,
        "threshold": threshold,
        "passed": bool(passed),
    })


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    paths = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    required = {
        "v1 panel": paths["ttm_challenger_v1_panel"],
        "v2 panel": paths["ttm_challenger_v2_panel"],
        "challenger summary": paths["ttm_challenger_summary"],
        "historical valuation summary": paths["ttm_history_summary"],
        "historical valuation coverage": paths["ttm_history_coverage_by_year"],
        "prices": (root / args.prices if not args.prices.is_absolute() else args.prices),
        "benchmark prices": (
            root / args.benchmark_prices
            if not args.benchmark_prices.is_absolute()
            else args.benchmark_prices
        ),
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing evaluation input(s):\n  " + "\n  ".join(missing))
    if paths["ttm_challenger_evaluation_dir"].exists():
        raise SystemExit(
            "Evaluation output already exists; preserve or rename it first: "
            f"{paths['ttm_challenger_evaluation_dir']}"
        )

    challenger_summary = json.loads(
        paths["ttm_challenger_summary"].read_text(encoding="utf-8")
    )
    if challenger_summary.get("status") != "TTM_FULL_CHALLENGER_BUILD_COMPLETE":
        raise SystemExit("Full challenger build is not complete")
    history_summary = json.loads(
        paths["ttm_history_summary"].read_text(encoding="utf-8")
    )
    if int(history_summary.get("pit_violations", 1)) != 0:
        raise SystemExit("Historical TTM valuation replay has PIT violations")

    v1 = pd.read_csv(paths["ttm_challenger_v1_panel"], low_memory=False)
    v2 = pd.read_csv(paths["ttm_challenger_v2_panel"], low_memory=False)
    price_store = BacktestPriceStore(required["prices"])
    benchmark_store = BacktestPriceStore(
        required["benchmark prices"],
        ticker_column="ticker",
    )

    print("V2 TTM CHALLENGER HISTORICAL EVALUATION")
    print("Running full-period V1...", flush=True)
    v1_full = _run_model(
        v1,
        store=price_store,
        model_id="long_growth_v1",
        score_column="long_growth_v1_score",
        eligible_column="top_conviction_eligible",
        start=TTM_CHALLENGER.evaluation_start,
        end=date(args.end_year, 12, 31),
    )
    print("Running full-period V2...", flush=True)
    v2_full = _run_model(
        v2,
        store=price_store,
        model_id=TTM_CHALLENGER.model_id,
        score_column=f"{TTM_CHALLENGER.model_id}_score",
        eligible_column="v2_top_conviction_eligible",
        start=TTM_CHALLENGER.evaluation_start,
        end=date(args.end_year, 12, 31),
    )
    common_dates = sorted(
        set(v1_full.weekly["decision_date"])
        & set(v2_full.weekly["decision_date"])
    )
    benchmark_full = run_single_asset_accumulation_backtest(
        price_store=benchmark_store,
        ticker=args.benchmark_symbol,
        decision_dates=common_dates,
        weekly_contribution=TTM_CHALLENGER.weekly_contribution,
        model_id=args.benchmark_symbol.upper(),
    )
    _validate_benchmark_result(
        benchmark_full,
        expected_decision_weeks=len(common_dates),
        benchmark_symbol=args.benchmark_symbol.upper(),
        scope="full-period",
    )

    turnover = _turnover_summary(v1, v2)
    concentration = _concentration_summary(v1, v2)

    print("Running 3-year and 5-year rolling windows...", flush=True)
    rolling, rolling_aggregate = _rolling(
        v1=v1,
        v2=v2,
        price_store=price_store,
        benchmark_store=benchmark_store,
        benchmark_symbol=args.benchmark_symbol,
        end_year=args.end_year,
    )

    coverage = pd.read_csv(
        paths["ttm_history_coverage_by_year"], low_memory=False
    )
    all_row = coverage.loc[coverage["year"].astype(str).eq("ALL")].iloc[0]
    overall_ratio = (
        float(all_row["ttm_valuation_eligible"])
        / float(all_row["annual_valuation_eligible"])
    )
    yearly = coverage.loc[
        ~coverage["year"].astype(str).eq("ALL")
    ].copy()
    yearly["year_numeric"] = pd.to_numeric(yearly["year"], errors="coerce")
    yearly = yearly.loc[
        yearly["year_numeric"].ge(TTM_CHALLENGER.evaluation_start.year)
    ]
    yearly["coverage_ratio"] = (
        pd.to_numeric(yearly["ttm_valuation_eligible"], errors="coerce")
        / pd.to_numeric(yearly["annual_valuation_eligible"], errors="coerce")
    )
    minimum_year_ratio = float(yearly["coverage_ratio"].min())

    turnover_index = turnover.set_index("variant")
    replacement_rate_delta = (
        float(turnover_index.loc["v2", "mean_replacement_rate"])
        - float(turnover_index.loc["v1", "mean_replacement_rate"])
    )

    thresholds = TTM_PROMOTION_THRESHOLDS
    gate_rows: list[dict[str, object]] = []
    _gate(
        gate_rows,
        name="historical_pit_violations",
        actual=int(history_summary["pit_violations"]),
        threshold=thresholds.max_pit_violations,
        passed=int(history_summary["pit_violations"]) <= thresholds.max_pit_violations,
        scope="integrity",
    )
    _gate(
        gate_rows,
        name="overall_valuation_coverage_ratio",
        actual=overall_ratio,
        threshold=thresholds.minimum_overall_valuation_coverage_ratio_vs_v1,
        passed=overall_ratio >= thresholds.minimum_overall_valuation_coverage_ratio_vs_v1,
        scope="coverage",
    )
    _gate(
        gate_rows,
        name="minimum_calendar_year_valuation_coverage_ratio",
        actual=minimum_year_ratio,
        threshold=thresholds.minimum_calendar_year_valuation_coverage_ratio_vs_v1,
        passed=minimum_year_ratio >= thresholds.minimum_calendar_year_valuation_coverage_ratio_vs_v1,
        scope="coverage",
    )
    _gate(
        gate_rows,
        name="median_weekly_full_model_top10_overlap",
        actual=float(challenger_summary["median_weekly_top10_overlap"]),
        threshold=thresholds.minimum_median_weekly_full_model_top10_overlap,
        passed=(
            float(challenger_summary["median_weekly_top10_overlap"])
            >= thresholds.minimum_median_weekly_full_model_top10_overlap
        ),
        scope="structure",
    )
    _gate(
        gate_rows,
        name="mean_top10_replacement_rate_increase",
        actual=replacement_rate_delta,
        threshold=thresholds.maximum_mean_top10_replacement_rate_increase,
        passed=(
            replacement_rate_delta
            <= thresholds.maximum_mean_top10_replacement_rate_increase
        ),
        scope="structure",
    )

    full_xirr_delta = (
        float(v2_full.summary["xirr"]) - float(v1_full.summary["xirr"])
    )
    drawdown_delta = (
        float(v2_full.summary["max_drawdown"])
        - float(v1_full.summary["max_drawdown"])
    )
    _gate(
        gate_rows,
        name="full_period_xirr_delta_v2_minus_v1",
        actual=full_xirr_delta,
        threshold=thresholds.minimum_full_period_xirr_delta,
        passed=full_xirr_delta >= thresholds.minimum_full_period_xirr_delta,
        scope="performance",
    )
    _gate(
        gate_rows,
        name="full_period_drawdown_increase",
        actual=drawdown_delta,
        threshold=thresholds.maximum_full_period_drawdown_increase,
        passed=drawdown_delta <= thresholds.maximum_full_period_drawdown_increase,
        scope="risk",
    )

    for row in rolling_aggregate.itertuples(index=False):
        prefix = f"{int(row.window_years)}y"
        _gate(
            gate_rows,
            name=f"{prefix}_rolling_xirr_win_rate",
            actual=float(row.v2_xirr_win_rate),
            threshold=thresholds.minimum_rolling_xirr_win_rate,
            passed=float(row.v2_xirr_win_rate) >= thresholds.minimum_rolling_xirr_win_rate,
            scope="rolling",
        )
        _gate(
            gate_rows,
            name=f"{prefix}_rolling_median_xirr_delta",
            actual=float(row.median_xirr_delta_v2_minus_v1),
            threshold=thresholds.minimum_rolling_median_xirr_delta,
            passed=float(row.median_xirr_delta_v2_minus_v1) >= thresholds.minimum_rolling_median_xirr_delta,
            scope="rolling",
        )
        _gate(
            gate_rows,
            name=f"{prefix}_benchmark_beating_window_count_delta",
            actual=int(row.benchmark_beating_window_count_delta),
            threshold=thresholds.minimum_benchmark_beating_window_count_delta,
            passed=int(row.benchmark_beating_window_count_delta) >= thresholds.minimum_benchmark_beating_window_count_delta,
            scope="benchmark",
        )

    gates = pd.DataFrame(gate_rows)
    historical_hard_gates_pass = bool(gates["passed"].all())

    summary_rows = []
    for result in (v1_full, v2_full, benchmark_full):
        summary_rows.append(dict(result.summary))
    backtest_summary = pd.DataFrame(summary_rows)

    weekly = pd.concat(
        [v1_full.weekly, v2_full.weekly, benchmark_full.weekly],
        ignore_index=True,
    )
    trades = pd.concat(
        [v1_full.trades, v2_full.trades, benchmark_full.trades],
        ignore_index=True,
    )

    summary = {
        "schema_version": 1,
        "status": (
            "HISTORICAL_HARD_GATES_PASS"
            if historical_hard_gates_pass
            else "HISTORICAL_HARD_GATES_FAIL"
        ),
        "model_id": TTM_CHALLENGER.model_id,
        "benchmark": args.benchmark_symbol.upper(),
        "evaluation_start": TTM_CHALLENGER.evaluation_start.isoformat(),
        "evaluation_end_year": args.end_year,
        "v1_xirr": float(v1_full.summary["xirr"]),
        "v2_xirr": float(v2_full.summary["xirr"]),
        "full_period_xirr_delta_v2_minus_v1": full_xirr_delta,
        "v1_max_drawdown": float(v1_full.summary["max_drawdown"]),
        "v2_max_drawdown": float(v2_full.summary["max_drawdown"]),
        "full_period_drawdown_delta_v2_minus_v1": drawdown_delta,
        "benchmark_xirr": float(benchmark_full.summary["xirr"]),
        "v1_xirr_vs_benchmark": (
            float(v1_full.summary["xirr"])
            - float(benchmark_full.summary["xirr"])
        ),
        "v2_xirr_vs_benchmark": (
            float(v2_full.summary["xirr"])
            - float(benchmark_full.summary["xirr"])
        ),
        "overall_valuation_coverage_ratio": overall_ratio,
        "minimum_calendar_year_valuation_coverage_ratio": minimum_year_ratio,
        "mean_top10_replacement_rate_increase": replacement_rate_delta,
        "historical_hard_gates_pass": historical_hard_gates_pass,
        "shadow_requirement_satisfied": False,
        "live_promotion_authorized": False,
        "promotion_thresholds": asdict(thresholds),
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    out = paths["ttm_challenger_evaluation_dir"]
    out.mkdir(parents=True, exist_ok=False)
    backtest_summary.to_csv(
        paths["ttm_challenger_backtest_summary"], index=False
    )
    weekly.to_csv(paths["ttm_challenger_backtest_weekly"], index=False)
    trades.to_csv(paths["ttm_challenger_backtest_trades"], index=False)
    turnover.to_csv(paths["ttm_challenger_turnover_summary"], index=False)
    concentration.to_csv(
        paths["ttm_challenger_concentration_summary"], index=False
    )
    rolling.to_csv(paths["ttm_challenger_rolling_summary"], index=False)
    rolling_aggregate.to_csv(
        paths["ttm_challenger_rolling_aggregate"], index=False
    )
    gates.to_csv(paths["ttm_challenger_gate_results"], index=False)
    paths["ttm_challenger_evaluation_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["ttm_challenger_evaluation_fingerprints"].write_text(
        json.dumps({
            "schema_version": 1,
            "inputs": fingerprint_files(root=root, paths=list(required.values())),
            "code": git_provenance(root),
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 TTM CHALLENGER HISTORICAL EVALUATION COMPLETE")
    print(
        f"V1 / V2 XIRR:              "
        f"{v1_full.summary['xirr']:.2%} / {v2_full.summary['xirr']:.2%}"
    )
    print(
        f"XIRR delta V2-V1:          {full_xirr_delta:+.2%}"
    )
    print(
        f"V1 / V2 max drawdown:      "
        f"{v1_full.summary['max_drawdown']:.2%} / "
        f"{v2_full.summary['max_drawdown']:.2%}"
    )
    print(
        f"Drawdown delta V2-V1:      {drawdown_delta:+.2%}"
    )
    print(
        f"{args.benchmark_symbol.upper()} XIRR:                   "
        f"{benchmark_full.summary['xirr']:.2%}"
    )
    for row in rolling_aggregate.itertuples(index=False):
        print(
            f"{int(row.window_years)}y windows: win rate="
            f"{row.v2_xirr_win_rate:.1%}, median XIRR delta="
            f"{row.median_xirr_delta_v2_minus_v1:+.2%}, "
            f"benchmark-count delta={int(row.benchmark_beating_window_count_delta):+d}"
        )
    print(
        f"Historical hard gates:     "
        f"{'PASS' if historical_hard_gates_pass else 'FAIL'}"
    )
    print(f"Output directory:          {out}")
    print("SHADOW REQUIREMENT IS NOT SATISFIED BY THIS COMMAND.")
    print("NO LIVE PROMOTION OR BROKER CAPABILITY EXISTS.")


if __name__ == "__main__":
    main()
