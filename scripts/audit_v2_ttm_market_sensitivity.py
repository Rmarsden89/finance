from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.backtest import (
    BacktestConfig,
    BacktestPriceStore,
    run_ranked_accumulation_backtest,
    run_single_asset_accumulation_backtest,
)
from finance.factors.validation import validate_raw_factors
from finance.factors.valuation import add_valuation_factors
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_challenger import add_long_growth_v2_ttm_scores
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER
from finance.research.ttm_valuation_family import (
    ANNUAL_VALUATION_WEIGHTS,
    add_ttm_valuation_factors,
    add_ttm_valuation_family_score,
    normalize_ttm_valuation_factors,
    score_weighted_family,
)
from finance.research.v2 import resolve_v2_ttm_diagnostic_paths
from finance.research.v2_impact import score_long_growth_panel
from finance.scoring.normalize import normalize_validated_factors



def _latest_rows_for_date(replay: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for concept, prefix in (
        ("revenue", "revenue"),
        ("net_income", "net_income"),
    ):
        subset = replay[
            [
                "cik",
                f"ttm_{prefix}_available_at",
                f"ttm_{prefix}_end_date",
            ]
        ].copy()
        subset = (
            subset.loc[subset["cik"].notna()]
            .drop_duplicates("cik", keep="last")
            .copy()
        )
        subset["concept"] = concept
        subset = subset.rename(
            columns={
                f"ttm_{prefix}_available_at": "available_at",
                f"ttm_{prefix}_end_date": "ttm_end_date",
            }
        )
        rows.append(subset)
    return pd.concat(rows, ignore_index=True)


def _merge_frozen_book_scores(
    result: pd.DataFrame,
    annual: pd.DataFrame,
) -> pd.DataFrame:
    book_cols = [
        "decision_date",
        "ticker",
        "book_to_market",
        "book_to_market_valid",
        "book_to_market_invalid_reason",
        "book_to_market_validated",
        "book_to_market_winsorized",
        "book_to_market_winsorized_flag",
        "book_to_market_percentile",
        "book_to_market_score",
    ]
    available_book = [
        column for column in book_cols if column in annual.columns
    ]

    left = result.copy()
    right = annual[available_book].copy()
    left["decision_date"] = pd.to_datetime(
        left["decision_date"], errors="raise"
    ).dt.normalize()
    right["decision_date"] = pd.to_datetime(
        right["decision_date"], errors="raise"
    ).dt.normalize()

    return left.drop(
        columns=[
            column
            for column in book_cols[2:]
            if column in left.columns
        ],
        errors="ignore",
    ).merge(
        right,
        on=["decision_date", "ticker"],
        how="left",
        validate="one_to_one",
    )


def _score_historical_ttm(
    panel: pd.DataFrame,
    replay: pd.DataFrame,
    annual: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    dates = sorted(
        pd.to_datetime(panel["decision_date"]).dt.date.unique()
    )
    panel_dates = pd.to_datetime(panel["decision_date"]).dt.date
    replay_dates = pd.to_datetime(replay["decision_date"]).dt.date

    for position, decision_day in enumerate(dates, start=1):
        snapshot = panel.loc[panel_dates.eq(decision_day)].copy()
        current_ttm = replay.loc[replay_dates.eq(decision_day)].copy()
        latest = _latest_rows_for_date(current_ttm)
        scored = add_ttm_valuation_factors(
            snapshot,
            current_ttm,
            latest,
        )
        frames.append(scored)

        if (
            position == 1
            or position % 50 == 0
            or position == len(dates)
        ):
            print(
                f"TTM factor build {position}/{len(dates)} "
                f"date={decision_day} rows={len(scored):,}",
                flush=True,
            )

    result = pd.concat(frames, ignore_index=True, sort=False)
    result = _merge_frozen_book_scores(result, annual)
    result = normalize_ttm_valuation_factors(result)
    return add_ttm_valuation_family_score(result)


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
            max_addon_position_weight=(
                TTM_CHALLENGER.max_addon_position_weight
            ),
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
            f"{scope} {benchmark_symbol} benchmark executed zero buys"
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
    for window_years in (3, 5):
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
                scope=(
                    f"{window_years}y "
                    f"{start_year}-{end_window_year}"
                ),
            )

            rows.append({
                "window_years": window_years,
                "window_start_year": start_year,
                "window_end_year": end_window_year,
                "v1_xirr": v1_result.summary["xirr"],
                "v2_xirr": v2_result.summary["xirr"],
                "xirr_delta_v2_minus_v1": (
                    v2_result.summary["xirr"]
                    - v1_result.summary["xirr"]
                ),
                "v1_max_drawdown": (
                    v1_result.summary["max_drawdown"]
                ),
                "v2_max_drawdown": (
                    v2_result.summary["max_drawdown"]
                ),
                "drawdown_delta_v2_minus_v1": (
                    v2_result.summary["max_drawdown"]
                    - v1_result.summary["max_drawdown"]
                ),
                "benchmark_xirr": benchmark.summary["xirr"],
                "v1_xirr_vs_benchmark": (
                    v1_result.summary["xirr"]
                    - benchmark.summary["xirr"]
                ),
                "v2_xirr_vs_benchmark": (
                    v2_result.summary["xirr"]
                    - benchmark.summary["xirr"]
                ),
            })

    detail = pd.DataFrame(rows)
    aggregate_rows = []
    for window_years, group in detail.groupby(
        "window_years",
        sort=True,
    ):
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild the frozen V1/V2 TTM challenger on the Issue #7 "
            "candidate-priced weekly panel and compare historical sensitivity."
        )
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date(2026, 9, 15),
    )
    parser.add_argument(
        "--candidate-panel",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "weekly_panel_sensitivity_v1/candidate_weekly_panel.csv"
        ),
    )
    parser.add_argument(
        "--candidate-prices",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v2/daily_prices.csv.gz"
        ),
    )
    parser.add_argument(
        "--benchmark-prices",
        type=Path,
        default=Path("data/market/benchmark_voo.csv"),
    )
    parser.add_argument("--benchmark-symbol", default="VOO")
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "ttm_model_sensitivity_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _top10(
    frame: pd.DataFrame,
    *,
    score_column: str,
    eligible_column: str,
) -> dict[pd.Timestamp, tuple[str, ...]]:
    data = frame.copy()
    data["decision_date"] = pd.to_datetime(
        data["decision_date"], errors="raise"
    ).dt.normalize()
    data[score_column] = pd.to_numeric(data[score_column], errors="coerce")
    result: dict[pd.Timestamp, tuple[str, ...]] = {}
    for decision_date, group in data.groupby("decision_date", sort=True):
        selected = group.loc[
            group[eligible_column].fillna(False).astype(bool)
            & group[score_column].notna()
        ].sort_values(
            [score_column, "ticker"],
            ascending=[False, True],
            kind="stable",
        ).head(TTM_CHALLENGER.top_n)
        result[decision_date] = tuple(
            selected["ticker"].astype(str).str.upper()
        )
    return result


def _selection_comparison(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    score_column: str,
    eligible_column: str,
) -> pd.DataFrame:
    left = _top10(
        baseline,
        score_column=score_column,
        eligible_column=eligible_column,
    )
    right = _top10(
        candidate,
        score_column=score_column,
        eligible_column=eligible_column,
    )
    rows = []
    for decision_date in sorted(set(left) | set(right)):
        before = left.get(decision_date, tuple())
        after = right.get(decision_date, tuple())
        rows.append({
            "decision_date": decision_date.date().isoformat(),
            "before_count": len(before),
            "after_count": len(after),
            "before_top10": "|".join(before),
            "after_top10": "|".join(after),
            "top10_overlap": len(set(before) & set(after)),
            "membership_changed": set(before) != set(after),
            "order_changed": before != after,
            "entered": "|".join(sorted(set(after) - set(before))),
            "exited": "|".join(sorted(set(before) - set(after))),
        })
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    paths = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    candidate_panel_path = _resolve(root, args.candidate_panel)
    candidate_prices = _resolve(root, args.candidate_prices)
    benchmark_prices = _resolve(root, args.benchmark_prices)
    output_dir = _resolve(root, args.output_dir)

    required = {
        "candidate panel": candidate_panel_path,
        "candidate prices": candidate_prices,
        "benchmark prices": benchmark_prices,
        "historical TTM numerators": paths["ttm_history_numerators"],
        "baseline V1 panel": paths["ttm_challenger_v1_panel"],
        "baseline V2 panel": paths["ttm_challenger_v2_panel"],
        "baseline evaluation summary": paths[
            "ttm_challenger_evaluation_summary"
        ],
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing TTM market sensitivity input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #7 TTM market sensitivity requires a clean tracked worktree"
        )
    if output_dir.exists():
        raise SystemExit(f"Output already exists; preserve it: {output_dir}")

    panel = pd.read_csv(candidate_panel_path, low_memory=False)
    replay = pd.read_csv(paths["ttm_history_numerators"], low_memory=False)
    baseline_v1 = pd.read_csv(paths["ttm_challenger_v1_panel"], low_memory=False)
    baseline_v2 = pd.read_csv(paths["ttm_challenger_v2_panel"], low_memory=False)
    baseline_eval = json.loads(
        paths["ttm_challenger_evaluation_summary"].read_text(encoding="utf-8")
    )

    print("Scoring candidate frozen V1...", flush=True)
    candidate_v1 = score_long_growth_panel(panel)

    print("Recomputing candidate annual valuation...", flush=True)
    annual = add_valuation_factors(panel)
    annual = validate_raw_factors(annual)
    annual = normalize_validated_factors(annual)
    annual = score_weighted_family(
        annual,
        weights=ANNUAL_VALUATION_WEIGHTS,
        output_prefix="annual_valuation",
        minimum_factors=2,
    )

    print("Recomputing candidate historical TTM valuation...", flush=True)
    candidate_ttm = _score_historical_ttm(panel, replay, annual)

    ttm_cols = [
        "decision_date",
        "ticker",
        "cik",
        "ttm_valuation_score",
        "ttm_valuation_eligible",
        "ttm_valuation_factor_count",
    ]
    candidate_v1["decision_date"] = pd.to_datetime(
        candidate_v1["decision_date"], errors="raise"
    ).dt.normalize()
    candidate_ttm["decision_date"] = pd.to_datetime(
        candidate_ttm["decision_date"], errors="raise"
    ).dt.normalize()

    merged = candidate_v1.merge(
        candidate_ttm[
            [column for column in ttm_cols if column in candidate_ttm.columns]
        ],
        on=["decision_date", "ticker", "cik"],
        how="left",
        validate="one_to_one",
    )
    candidate_v2 = add_long_growth_v2_ttm_scores(merged)

    v1_selection = _selection_comparison(
        baseline_v1,
        candidate_v1,
        score_column="long_growth_v1_score",
        eligible_column="top_conviction_eligible",
    )
    v2_selection = _selection_comparison(
        baseline_v2,
        candidate_v2,
        score_column=f"{TTM_CHALLENGER.model_id}_score",
        eligible_column="v2_top_conviction_eligible",
    )

    price_store = BacktestPriceStore(candidate_prices)
    benchmark_store = BacktestPriceStore(
        benchmark_prices,
        ticker_column="ticker",
    )

    print("Running candidate full-period V1...", flush=True)
    v1_full = _run_model(
        candidate_v1,
        store=price_store,
        model_id="long_growth_v1_issue7_candidate",
        score_column="long_growth_v1_score",
        eligible_column="top_conviction_eligible",
        start=TTM_CHALLENGER.evaluation_start,
        end=date(args.end_year, 12, 31),
    )
    print("Running candidate full-period V2...", flush=True)
    v2_full = _run_model(
        candidate_v2,
        store=price_store,
        model_id=f"{TTM_CHALLENGER.model_id}_issue7_candidate",
        score_column=f"{TTM_CHALLENGER.model_id}_score",
        eligible_column="v2_top_conviction_eligible",
        start=TTM_CHALLENGER.evaluation_start,
        end=date(args.end_year, 12, 31),
    )

    print("Running candidate rolling windows...", flush=True)
    rolling, rolling_aggregate = _rolling(
        v1=candidate_v1,
        v2=candidate_v2,
        price_store=price_store,
        benchmark_store=benchmark_store,
        benchmark_symbol=args.benchmark_symbol,
        end_year=args.end_year,
    )

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_TTM_MODEL_SENSITIVITY_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "v1_selection_changed_weeks": int(v1_selection["membership_changed"].sum()),
        "v2_selection_changed_weeks": int(v2_selection["membership_changed"].sum()),
        "candidate_v1_xirr": float(v1_full.summary["xirr"]),
        "candidate_v2_xirr": float(v2_full.summary["xirr"]),
        "candidate_xirr_delta_v2_minus_v1": (
            float(v2_full.summary["xirr"])
            - float(v1_full.summary["xirr"])
        ),
        "candidate_v1_max_drawdown": float(
            v1_full.summary["max_drawdown"]
        ),
        "candidate_v2_max_drawdown": float(
            v2_full.summary["max_drawdown"]
        ),
        "baseline_v1_xirr": float(baseline_eval["v1_xirr"]),
        "baseline_v2_xirr": float(baseline_eval["v2_xirr"]),
        "baseline_v1_max_drawdown": float(
            baseline_eval["v1_max_drawdown"]
        ),
        "baseline_v2_max_drawdown": float(
            baseline_eval["v2_max_drawdown"]
        ),
        "candidate_minus_baseline_v1_xirr": (
            float(v1_full.summary["xirr"])
            - float(baseline_eval["v1_xirr"])
        ),
        "candidate_minus_baseline_v2_xirr": (
            float(v2_full.summary["xirr"])
            - float(baseline_eval["v2_xirr"])
        ),
        "candidate_minus_baseline_v1_drawdown": (
            float(v1_full.summary["max_drawdown"])
            - float(baseline_eval["v1_max_drawdown"])
        ),
        "candidate_minus_baseline_v2_drawdown": (
            float(v2_full.summary["max_drawdown"])
            - float(baseline_eval["v2_max_drawdown"])
        ),
        "production_canonical_modified": False,
        "live_promotion_authorized": False,
    }

    output_dir.mkdir(parents=True)
    candidate_v1.to_csv(output_dir / "candidate_v1_panel.csv", index=False)
    candidate_v2.to_csv(output_dir / "candidate_v2_panel.csv", index=False)
    candidate_ttm.to_csv(
        output_dir / "candidate_ttm_valuation_panel.csv", index=False
    )
    v1_selection.to_csv(
        output_dir / "v1_selection_comparison.csv", index=False
    )
    v2_selection.to_csv(
        output_dir / "v2_selection_comparison.csv", index=False
    )
    rolling.to_csv(output_dir / "rolling_window_summary.csv", index=False)
    rolling_aggregate.to_csv(
        output_dir / "rolling_window_aggregate.csv", index=False
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "inputs": fingerprint_files(
                root=root,
                paths=list(required.values()),
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("ISSUE #7 TTM MODEL SENSITIVITY")
    print(
        f"V1 changed Top-10 weeks:      "
        f"{int(v1_selection['membership_changed'].sum())}/{len(v1_selection)}"
    )
    print(
        f"V2 changed Top-10 weeks:      "
        f"{int(v2_selection['membership_changed'].sum())}/{len(v2_selection)}"
    )
    print(
        f"Baseline V1 / candidate V1 XIRR: "
        f"{baseline_eval['v1_xirr']:.2%} / {v1_full.summary['xirr']:.2%}"
    )
    print(
        f"Baseline V2 / candidate V2 XIRR: "
        f"{baseline_eval['v2_xirr']:.2%} / {v2_full.summary['xirr']:.2%}"
    )
    print(
        f"Candidate V2-V1 XIRR delta:  "
        f"{summary['candidate_xirr_delta_v2_minus_v1']:+.2%}"
    )
    print(
        f"Baseline V1 / candidate V1 DD: "
        f"{baseline_eval['v1_max_drawdown']:.2%} / "
        f"{v1_full.summary['max_drawdown']:.2%}"
    )
    print(
        f"Baseline V2 / candidate V2 DD: "
        f"{baseline_eval['v2_max_drawdown']:.2%} / "
        f"{v2_full.summary['max_drawdown']:.2%}"
    )
    for row in rolling_aggregate.itertuples(index=False):
        print(
            f"{int(row.window_years)}y candidate windows: "
            f"V2 win rate={row.v2_xirr_win_rate:.1%}, "
            f"median delta={row.median_xirr_delta_v2_minus_v1:+.2%}, "
            f"benchmark-count delta="
            f"{int(row.benchmark_beating_window_count_delta):+d}"
        )
    print()
    print(f"Output directory:             {output_dir}")
    print("PRODUCTION CANONICAL MARKET DATA WAS NOT MODIFIED.")
    print("NO LIVE PROMOTION OR BROKER CAPABILITY EXISTS.")


if __name__ == "__main__":
    main()
