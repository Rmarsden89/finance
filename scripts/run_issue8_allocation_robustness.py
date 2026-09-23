from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from finance.backtest import (
    BacktestPriceStore,
    run_single_asset_accumulation_backtest,
)
from finance.research.allocation_backtest import (
    AllocationBacktestConfig,
    run_allocation_backtest,
)
from finance.research.allocation_challengers import (
    ALLOCATION_EXPERIMENT,
    RULES,
)
from finance.research.fingerprints import (
    fingerprint_files,
    git_provenance,
)
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the predeclared Issue #8 allocation robustness suite. "
            "Research-only; frozen formulas and ranked inputs are unchanged."
        )
    )
    parser.add_argument(
        "--freeze-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/"
            "issue8_allocation_challengers/frozen_v1"
        ),
    )
    parser.add_argument(
        "--primary-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/"
            "issue8_allocation_challengers/primary_v1"
        ),
    )
    parser.add_argument(
        "--issue19-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/"
            "issue19_market_promotion/promotion_v1"
        ),
    )
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/"
            "issue8_allocation_challengers/robustness_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _model_specs() -> dict[str, dict[str, str]]:
    return {
        "v1": {
            "score_column": "long_growth_v1_score",
            "eligible_column": "top_conviction_eligible",
        },
        "v2": {
            "score_column": f"{TTM_CHALLENGER.model_id}_score",
            "eligible_column": "v2_top_conviction_eligible",
        },
    }


def _config(
    *,
    rule_id: str,
    eligible_column: str,
    allocation_score_column: str | None = None,
    allocation_rank_column: str | None = None,
) -> AllocationBacktestConfig:
    return AllocationBacktestConfig(
        weekly_contribution=ALLOCATION_EXPERIMENT.weekly_contribution,
        top_n=ALLOCATION_EXPERIMENT.top_n,
        selection_flag=eligible_column,
        max_addon_position_weight=(
            ALLOCATION_EXPERIMENT.max_addon_position_weight
        ),
        rule_id=rule_id,
        allocation_score_column=allocation_score_column,
        allocation_rank_column=allocation_rank_column,
    )


def _run(
    frame: pd.DataFrame,
    *,
    store: BacktestPriceStore,
    model_name: str,
    rule_id: str,
    score_column: str,
    eligible_column: str,
    start: date,
    end: date,
    allocation_score_column: str | None = None,
    allocation_rank_column: str | None = None,
    suffix: str = "",
):
    return run_allocation_backtest(
        frame,
        price_store=store,
        model_id=f"{model_name}_{rule_id}{suffix}",
        score_column=score_column,
        config=_config(
            rule_id=rule_id,
            eligible_column=eligible_column,
            allocation_score_column=allocation_score_column,
            allocation_rank_column=allocation_rank_column,
        ),
        start=start,
        end=end,
    )


def _primary_lookup(summary: pd.DataFrame) -> dict[tuple[str, str], dict[str, object]]:
    return {
        (str(row["model_variant"]), str(row["allocation_rule"])): row
        for row in summary.to_dict("records")
    }


def _winner_attribution(
    trades: pd.DataFrame,
    *,
    price_store: BacktestPriceStore,
    terminal_date: date,
) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "buy_dollars",
                "forced_exit_proceeds",
                "ending_units",
                "terminal_holding_value",
                "terminal_gain_contribution",
            ]
        )

    rows = []
    for ticker, group in trades.groupby("ticker", sort=True):
        buys = group.loc[group["side"].eq("buy")]
        forced = group.loc[group["side"].eq("forced_exit")]
        buy_dollars = float(
            pd.to_numeric(buys["dollars"], errors="coerce").fillna(0).sum()
        )
        forced_proceeds = float(
            pd.to_numeric(forced["dollars"], errors="coerce").fillna(0).sum()
        )
        buy_units = float(
            pd.to_numeric(buys["units"], errors="coerce").fillna(0).sum()
        )
        forced_units = float(
            pd.to_numeric(forced["units"], errors="coerce").fillna(0).sum()
        )
        ending_units = buy_units - forced_units
        quote = price_store.latest_as_of(str(ticker), terminal_date)
        terminal_value = (
            ending_units * quote.mark_price
            if quote is not None and ending_units > 0
            else 0.0
        )
        rows.append({
            "ticker": str(ticker),
            "buy_dollars": buy_dollars,
            "forced_exit_proceeds": forced_proceeds,
            "ending_units": ending_units,
            "terminal_holding_value": terminal_value,
            "terminal_gain_contribution": (
                forced_proceeds + terminal_value - buy_dollars
            ),
        })
    return pd.DataFrame(rows).sort_values(
        ["terminal_gain_contribution", "ticker"],
        ascending=[False, True],
        kind="stable",
    )


def _score_sensitivity_frame(
    frame: pd.DataFrame,
    *,
    score_column: str,
    eligible_column: str,
    multiplier: float,
) -> pd.DataFrame:
    result = frame.copy()
    result["issue8_allocation_score"] = pd.to_numeric(
        result[score_column], errors="coerce"
    )
    dates = pd.to_datetime(
        result["decision_date"], errors="raise"
    ).dt.date
    result["_issue8_date"] = dates

    for _, idx in result.groupby("_issue8_date", sort=True).groups.items():
        group = result.loc[idx]
        selected = group.loc[
            group[eligible_column].fillna(False).astype(bool)
            & group[score_column].notna()
        ].sort_values(score_column, ascending=False).head(
            ALLOCATION_EXPERIMENT.top_n
        )
        if selected.empty:
            continue
        raw = pd.to_numeric(selected[score_column], errors="raise")
        mean = float(raw.mean())
        transformed = mean + multiplier * (raw - mean)
        result.loc[selected.index, "issue8_allocation_score"] = transformed

    return result.drop(columns=["_issue8_date"])


def _rank_sensitivity_frame(
    frame: pd.DataFrame,
    *,
    score_column: str,
    eligible_column: str,
    swap: tuple[int, int],
) -> pd.DataFrame:
    result = frame.copy()
    result["issue8_allocation_rank"] = np.nan
    dates = pd.to_datetime(
        result["decision_date"], errors="raise"
    ).dt.date
    result["_issue8_date"] = dates

    for _, idx in result.groupby("_issue8_date", sort=True).groups.items():
        group = result.loc[idx]
        selected = group.loc[
            group[eligible_column].fillna(False).astype(bool)
            & group[score_column].notna()
        ].sort_values(score_column, ascending=False).head(
            ALLOCATION_EXPERIMENT.top_n
        )
        ranks = list(range(1, len(selected) + 1))
        left, right = swap
        ranks = [
            right if rank == left else left if rank == right else rank
            for rank in ranks
        ]
        result.loc[selected.index, "issue8_allocation_rank"] = ranks

    return result.drop(columns=["_issue8_date"])


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    freeze_dir = _resolve(root, args.freeze_dir)
    primary_dir = _resolve(root, args.primary_dir)
    issue19_dir = _resolve(root, args.issue19_dir)
    prices = _resolve(root, args.prices)
    benchmark_prices = _resolve(root, args.benchmark_prices)
    output_dir = _resolve(root, args.output_dir)

    freeze_manifest = freeze_dir / "experiment_manifest.json"
    primary_manifest = primary_dir / "result_manifest.json"
    primary_summary_path = primary_dir / "primary_summary.csv"
    primary_trades_path = primary_dir / "primary_trades.csv"
    ranked_v1 = (
        issue19_dir
        / "ttm_model_verification"
        / "candidate_v1_panel.csv"
    )
    ranked_v2 = (
        issue19_dir
        / "ttm_model_verification"
        / "candidate_v2_panel.csv"
    )

    required = {
        "freeze manifest": freeze_manifest,
        "primary manifest": primary_manifest,
        "primary summary": primary_summary_path,
        "primary trades": primary_trades_path,
        "frozen V1 panel": ranked_v1,
        "frozen V2 panel": ranked_v2,
        "canonical prices": prices,
        "benchmark prices": benchmark_prices,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing Issue #8 robustness input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #8 robustness suite requires a clean tracked worktree"
        )
    if output_dir.exists():
        raise SystemExit(
            f"Issue #8 robustness output already exists: {output_dir}"
        )

    frozen = json.loads(freeze_manifest.read_text(encoding="utf-8"))
    primary_manifest_data = json.loads(
        primary_manifest.read_text(encoding="utf-8")
    )
    if frozen.get("status") != "ISSUE8_EXPERIMENT_FROZEN":
        raise SystemExit("Issue #8 experiment is not frozen")
    if primary_manifest_data.get("status") != (
        "ISSUE8_PRIMARY_COMPARISON_COMPLETE"
    ):
        raise SystemExit("Issue #8 primary comparison is not complete")
    if not bool(primary_manifest_data.get("equal_dollar_parity_passed")):
        raise SystemExit("Issue #8 primary equal-dollar parity did not pass")

    v1 = pd.read_csv(ranked_v1, low_memory=False)
    v2 = pd.read_csv(ranked_v2, low_memory=False)
    frames = {"v1": v1, "v2": v2}
    specs = _model_specs()
    store = BacktestPriceStore(prices)
    benchmark_store = BacktestPriceStore(
        benchmark_prices,
        ticker_column="ticker",
    )

    primary_summary = pd.read_csv(primary_summary_path, low_memory=False)
    primary_lookup = _primary_lookup(primary_summary)
    primary_trades = pd.read_csv(primary_trades_path, low_memory=False)

    print("ISSUE #8 ALLOCATION ROBUSTNESS SUITE")

    # 1) Rolling 3y / 5y.
    print("Running rolling 3y/5y windows...", flush=True)
    rolling_rows = []
    benchmark_cache: dict[tuple[int, int], float] = {}
    for window_years in ALLOCATION_EXPERIMENT.rolling_window_years:
        final_start = args.end_year - window_years + 1
        for start_year in range(
            ALLOCATION_EXPERIMENT.evaluation_start_year,
            final_start + 1,
        ):
            end_year = start_year + window_years - 1
            start = date(start_year, 1, 1)
            end = date(end_year, 12, 31)

            # Benchmark dates are common across model/rule runs because ranked
            # input decision dates are frozen and identical by model scope.
            benchmark_key = (start_year, end_year)
            benchmark_xirr = benchmark_cache.get(benchmark_key)
            if benchmark_xirr is None:
                decision_dates = sorted(
                    set(
                        pd.to_datetime(
                            v1["decision_date"], errors="raise"
                        ).dt.date
                    )
                )
                decision_dates = [
                    d for d in decision_dates if start <= d <= end
                ]
                benchmark = run_single_asset_accumulation_backtest(
                    price_store=benchmark_store,
                    ticker=args.benchmark_symbol.upper(),
                    decision_dates=decision_dates,
                    weekly_contribution=(
                        ALLOCATION_EXPERIMENT.weekly_contribution
                    ),
                    model_id=args.benchmark_symbol.upper(),
                )
                if int(benchmark.summary.get("buy_count", 0)) == 0:
                    raise SystemExit(
                        f"{args.benchmark_symbol.upper()} benchmark "
                        f"executed zero buys for {start_year}-{end_year}"
                    )
                benchmark_xirr = float(benchmark.summary["xirr"])
                benchmark_cache[benchmark_key] = benchmark_xirr

            for model_name in ("v1", "v2"):
                frame = frames[model_name]
                spec = specs[model_name]
                for rule in RULES:
                    result = _run(
                        frame,
                        store=store,
                        model_name=model_name,
                        rule_id=rule.rule_id,
                        score_column=spec["score_column"],
                        eligible_column=spec["eligible_column"],
                        start=start,
                        end=end,
                    )
                    rolling_rows.append({
                        "model_variant": model_name,
                        "allocation_rule": rule.rule_id,
                        "window_years": window_years,
                        "window_start_year": start_year,
                        "window_end_year": end_year,
                        "xirr": float(result.summary["xirr"]),
                        "max_drawdown": float(
                            result.summary["max_drawdown"]
                        ),
                        "annualized_time_weighted_return": float(
                            result.summary[
                                "annualized_time_weighted_return"
                            ]
                        ),
                        "benchmark_xirr": benchmark_xirr,
                        "xirr_vs_benchmark": (
                            float(result.summary["xirr"])
                            - benchmark_xirr
                        ),
                    })

    rolling = pd.DataFrame(rolling_rows)
    equal_rolling = (
        rolling.loc[rolling["allocation_rule"].eq("equal_dollar")]
        .set_index(
            [
                "model_variant",
                "window_years",
                "window_start_year",
                "window_end_year",
            ]
        )["xirr"]
        .to_dict()
    )
    rolling["xirr_delta_vs_equal"] = rolling.apply(
        lambda row: (
            float(row["xirr"])
            - float(equal_rolling[
                (
                    row["model_variant"],
                    row["window_years"],
                    row["window_start_year"],
                    row["window_end_year"],
                )
            ])
        ),
        axis=1,
    )
    rolling_aggregate = (
        rolling.groupby(
            ["model_variant", "allocation_rule", "window_years"],
            as_index=False,
        )
        .agg(
            windows=("xirr", "size"),
            mean_xirr=("xirr", "mean"),
            median_xirr=("xirr", "median"),
            mean_delta_vs_equal=("xirr_delta_vs_equal", "mean"),
            median_delta_vs_equal=("xirr_delta_vs_equal", "median"),
            equal_win_or_tie_rate=(
                "xirr_delta_vs_equal",
                lambda x: float(pd.Series(x).ge(0).mean()),
            ),
            benchmark_win_rate=(
                "xirr_vs_benchmark",
                lambda x: float(pd.Series(x).gt(0).mean()),
            ),
            mean_max_drawdown=("max_drawdown", "mean"),
            worst_max_drawdown=("max_drawdown", "max"),
        )
    )

    # 2) Leave-winner-out.
    print("Running leave-winner-out diagnostics...", flush=True)
    leave_rows = []
    attribution_frames = []
    for model_name in ("v1", "v2"):
        frame = frames[model_name]
        spec = specs[model_name]
        for rule in RULES:
            model_id = f"{model_name}_{rule.rule_id}"
            trades = primary_trades.loc[
                primary_trades["model_id"].eq(model_id)
            ].copy()
            primary_row = primary_lookup[(model_name, rule.rule_id)]
            terminal_date = date.fromisoformat(
                str(primary_row["terminal_date"])[:10]
            )
            attribution = _winner_attribution(
                trades,
                price_store=store,
                terminal_date=terminal_date,
            )
            if attribution.empty:
                raise SystemExit(
                    f"No winner attribution available for {model_id}"
                )
            attribution.insert(0, "model_variant", model_name)
            attribution.insert(1, "allocation_rule", rule.rule_id)
            attribution_frames.append(attribution)

            winner = str(attribution.iloc[0]["ticker"])
            modified = frame.copy()
            modified.loc[
                modified["ticker"].astype(str).str.upper().eq(winner),
                spec["eligible_column"],
            ] = False

            result = _run(
                modified,
                store=store,
                model_name=model_name,
                rule_id=rule.rule_id,
                score_column=spec["score_column"],
                eligible_column=spec["eligible_column"],
                start=date(
                    ALLOCATION_EXPERIMENT.evaluation_start_year,
                    1,
                    1,
                ),
                end=date(args.end_year, 12, 31),
                suffix="_leave_winner_out",
            )
            original_xirr = float(primary_row["xirr"])
            leave_rows.append({
                "model_variant": model_name,
                "allocation_rule": rule.rule_id,
                "excluded_winner": winner,
                "winner_terminal_gain_contribution": float(
                    attribution.iloc[0]["terminal_gain_contribution"]
                ),
                "original_xirr": original_xirr,
                "leave_winner_out_xirr": float(result.summary["xirr"]),
                "xirr_delta_leaveout_minus_original": (
                    float(result.summary["xirr"]) - original_xirr
                ),
                "original_max_drawdown": float(
                    primary_row["max_drawdown"]
                ),
                "leave_winner_out_max_drawdown": float(
                    result.summary["max_drawdown"]
                ),
            })
    leaveout = pd.DataFrame(leave_rows)
    attribution_all = pd.concat(
        attribution_frames, ignore_index=True
    )

    # 3) Score compression / expansion. Only score_weighted should react.
    print("Running score compression/expansion sensitivity...", flush=True)
    score_rows = []
    score_multipliers = (
        *ALLOCATION_EXPERIMENT.score_compression_multipliers,
        *ALLOCATION_EXPERIMENT.score_expansion_multipliers,
    )
    for model_name in ("v1", "v2"):
        frame = frames[model_name]
        spec = specs[model_name]
        base_xirr = float(
            primary_lookup[(model_name, "score_weighted")]["xirr"]
        )
        for multiplier in score_multipliers:
            modified = _score_sensitivity_frame(
                frame,
                score_column=spec["score_column"],
                eligible_column=spec["eligible_column"],
                multiplier=multiplier,
            )
            result = _run(
                modified,
                store=store,
                model_name=model_name,
                rule_id="score_weighted",
                score_column=spec["score_column"],
                eligible_column=spec["eligible_column"],
                allocation_score_column="issue8_allocation_score",
                start=date(
                    ALLOCATION_EXPERIMENT.evaluation_start_year,
                    1,
                    1,
                ),
                end=date(args.end_year, 12, 31),
                suffix=f"_score_{multiplier:.2f}",
            )
            score_rows.append({
                "model_variant": model_name,
                "multiplier": multiplier,
                "xirr": float(result.summary["xirr"]),
                "xirr_delta_vs_primary_score_weighted": (
                    float(result.summary["xirr"]) - base_xirr
                ),
                "max_drawdown": float(
                    result.summary["max_drawdown"]
                ),
                "mean_contribution_hhi": float(
                    result.summary["mean_contribution_hhi"]
                ),
                "mean_effective_contribution_names": float(
                    result.summary[
                        "mean_effective_contribution_names"
                    ]
                ),
            })
    score_sensitivity = pd.DataFrame(score_rows)

    # 4) Rank perturbation. Run every rule; equal and score are invariance controls.
    print("Running rank perturbation sensitivity...", flush=True)
    rank_rows = []
    for model_name in ("v1", "v2"):
        frame = frames[model_name]
        spec = specs[model_name]
        for swap in ALLOCATION_EXPERIMENT.rank_perturbation_swaps:
            modified = _rank_sensitivity_frame(
                frame,
                score_column=spec["score_column"],
                eligible_column=spec["eligible_column"],
                swap=swap,
            )
            for rule in RULES:
                primary_xirr = float(
                    primary_lookup[(model_name, rule.rule_id)]["xirr"]
                )
                result = _run(
                    modified,
                    store=store,
                    model_name=model_name,
                    rule_id=rule.rule_id,
                    score_column=spec["score_column"],
                    eligible_column=spec["eligible_column"],
                    allocation_rank_column="issue8_allocation_rank",
                    start=date(
                        ALLOCATION_EXPERIMENT.evaluation_start_year,
                        1,
                        1,
                    ),
                    end=date(args.end_year, 12, 31),
                    suffix=f"_swap_{swap[0]}_{swap[1]}",
                )
                rank_rows.append({
                    "model_variant": model_name,
                    "allocation_rule": rule.rule_id,
                    "swap_left": swap[0],
                    "swap_right": swap[1],
                    "xirr": float(result.summary["xirr"]),
                    "xirr_delta_vs_primary": (
                        float(result.summary["xirr"]) - primary_xirr
                    ),
                    "max_drawdown": float(
                        result.summary["max_drawdown"]
                    ),
                    "mean_contribution_hhi": float(
                        result.summary["mean_contribution_hhi"]
                    ),
                })
    rank_sensitivity = pd.DataFrame(rank_rows)

    # Invariance controls: equal-dollar and score-weighted ignore rank labels.
    controls = rank_sensitivity.loc[
        rank_sensitivity["allocation_rule"].isin(
            ["equal_dollar", "score_weighted"]
        )
    ]
    if not controls["xirr_delta_vs_primary"].abs().le(1e-12).all():
        raise SystemExit(
            "Rank perturbation invariance control failed for "
            "equal-dollar or score-weighted"
        )

    output_dir.mkdir(parents=True)
    rolling.to_csv(output_dir / "rolling_detail.csv", index=False)
    rolling_aggregate.to_csv(
        output_dir / "rolling_aggregate.csv", index=False
    )
    leaveout.to_csv(
        output_dir / "leave_winner_out.csv", index=False
    )
    attribution_all.to_csv(
        output_dir / "winner_attribution.csv", index=False
    )
    score_sensitivity.to_csv(
        output_dir / "score_sensitivity.csv", index=False
    )
    rank_sensitivity.to_csv(
        output_dir / "rank_sensitivity.csv", index=False
    )

    manifest = {
        "schema_version": 1,
        "status": "ISSUE8_ROBUSTNESS_COMPLETE",
        "experiment_id": ALLOCATION_EXPERIMENT.experiment_id,
        "rolling_windows": list(
            ALLOCATION_EXPERIMENT.rolling_window_years
        ),
        "score_multipliers": list(score_multipliers),
        "rank_swaps": [
            list(pair)
            for pair in ALLOCATION_EXPERIMENT.rank_perturbation_swaps
        ],
        "leave_winner_out_count": (
            ALLOCATION_EXPERIMENT.leave_winner_out_count
        ),
        "rank_invariance_controls_passed": True,
        "live_rule_changed": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
        "inputs": fingerprint_files(
            root=root,
            paths=list(required.values()),
        ),
        "code": provenance,
    }
    (output_dir / "result_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("ROLLING AGGREGATE")
    for row in rolling_aggregate.itertuples(index=False):
        print(
            f"{row.model_variant.upper()} "
            f"{row.allocation_rule:<18} "
            f"{int(row.window_years)}y "
            f"med_dEq={row.median_delta_vs_equal:+.2%} "
            f"win/tie={row.equal_win_or_tie_rate:.1%} "
            f"benchWin={row.benchmark_win_rate:.1%} "
            f"worstDD={row.worst_max_drawdown:.2%}"
        )

    print()
    print("LEAVE-WINNER-OUT")
    for row in leaveout.itertuples(index=False):
        print(
            f"{row.model_variant.upper()} "
            f"{row.allocation_rule:<18} "
            f"winner={row.excluded_winner:<6} "
            f"XIRR={row.original_xirr:.2%}->"
            f"{row.leave_winner_out_xirr:.2%} "
            f"delta={row.xirr_delta_leaveout_minus_original:+.2%}"
        )

    print()
    print("SCORE DISPERSION")
    for row in score_sensitivity.itertuples(index=False):
        print(
            f"{row.model_variant.upper()} "
            f"m={row.multiplier:.2f} "
            f"XIRR={row.xirr:.2%} "
            f"dPrimary={row.xirr_delta_vs_primary_score_weighted:+.2%} "
            f"ContribHHI={row.mean_contribution_hhi:.4f} "
            f"EffN={row.mean_effective_contribution_names:.2f}"
        )

    print()
    print("RANK PERTURBATION")
    affected = rank_sensitivity.loc[
        rank_sensitivity["allocation_rule"].isin(
            ["rank_weighted", "conviction_bands"]
        )
    ]
    for row in affected.itertuples(index=False):
        print(
            f"{row.model_variant.upper()} "
            f"{row.allocation_rule:<18} "
            f"swap={int(row.swap_left)}/{int(row.swap_right)} "
            f"XIRR={row.xirr:.2%} "
            f"dPrimary={row.xirr_delta_vs_primary:+.2%}"
        )

    print()
    print("Rank invariance controls:     PASS")
    print(f"Output directory:             {output_dir}")
    print("NO ALLOCATION WINNER WAS SELECTED.")
    print("NO LIVE, BROKER, OR ORDER RULE WAS CHANGED.")


if __name__ == "__main__":
    main()
