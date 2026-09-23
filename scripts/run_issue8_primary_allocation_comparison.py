from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path

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
            "Run the frozen Issue #8 primary allocation comparison on the "
            "promoted canonical baseline. Research-only."
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
            "issue8_allocation_challengers/primary_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _sha_for_suffix(bundle: dict[str, object], suffix: str) -> str:
    for row in bundle.get("files", []):
        if str(row.get("path", "")).endswith(suffix):
            return str(row["sha256"])
    raise SystemExit(f"Fingerprint bundle lacks {suffix}")


def _require_close(
    actual: float,
    expected: float,
    *,
    label: str,
    tolerance: float = 1e-12,
) -> None:
    if not math.isclose(
        float(actual),
        float(expected),
        rel_tol=0,
        abs_tol=tolerance,
    ):
        raise SystemExit(
            f"{label} mismatch: expected {expected!r}, got {actual!r}"
        )


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


def _benchmark(
    *,
    price_store: BacktestPriceStore,
    symbol: str,
    decision_dates: list[date],
):
    result = run_single_asset_accumulation_backtest(
        price_store=price_store,
        ticker=symbol,
        decision_dates=decision_dates,
        weekly_contribution=ALLOCATION_EXPERIMENT.weekly_contribution,
        model_id=symbol.upper(),
    )
    if int(result.summary.get("buy_count", 0)) == 0:
        raise SystemExit(
            f"{symbol.upper()} benchmark executed zero buys"
        )
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    freeze_dir = _resolve(root, args.freeze_dir)
    issue19_dir = _resolve(root, args.issue19_dir)
    prices = _resolve(root, args.prices)
    benchmark_prices = _resolve(root, args.benchmark_prices)
    output_dir = _resolve(root, args.output_dir)

    freeze_manifest_path = freeze_dir / "experiment_manifest.json"
    issue19_state_path = issue19_dir / "promotion_state.json"
    issue19_ttm_path = (
        issue19_dir / "ttm_model_verification" / "summary.json"
    )
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
        "frozen experiment manifest": freeze_manifest_path,
        "Issue #19 promotion state": issue19_state_path,
        "Issue #19 TTM summary": issue19_ttm_path,
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
            "Missing Issue #8 primary input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #8 primary comparison requires a clean tracked worktree"
        )
    if output_dir.exists():
        raise SystemExit(
            f"Issue #8 primary output already exists: {output_dir}"
        )

    frozen = json.loads(
        freeze_manifest_path.read_text(encoding="utf-8")
    )
    if frozen.get("status") != "ISSUE8_EXPERIMENT_FROZEN":
        raise SystemExit("Issue #8 experiment is not frozen")
    if frozen.get("experiment", {}).get("experiment_id") != (
        ALLOCATION_EXPERIMENT.experiment_id
    ):
        raise SystemExit("Frozen experiment ID does not match source code")

    ranked_now = fingerprint_files(
        root=root,
        paths=[ranked_v1, ranked_v2],
    )
    frozen_ranked = frozen.get("ranked_inputs", {})
    for suffix in ("candidate_v1_panel.csv", "candidate_v2_panel.csv"):
        expected = _sha_for_suffix(frozen_ranked, suffix)
        actual = _sha_for_suffix(ranked_now, suffix)
        if actual != expected:
            raise SystemExit(
                f"Frozen ranked input drift for {suffix}: "
                f"{actual} != {expected}"
            )

    promotion = json.loads(
        issue19_state_path.read_text(encoding="utf-8")
    )
    if promotion.get("status") != "PROMOTION_COMPLETE":
        raise SystemExit("Issue #19 promotion is not complete")

    canonical_now = fingerprint_files(root=root, paths=[prices])
    promoted_prices_sha = _sha_for_suffix(
        promotion.get("post_promotion", {}),
        "daily_prices.csv.gz",
    )
    current_prices_sha = _sha_for_suffix(
        canonical_now,
        "daily_prices.csv.gz",
    )
    if current_prices_sha != promoted_prices_sha:
        raise SystemExit(
            "Canonical price baseline drifted after Issue #19"
        )

    issue19_ttm = json.loads(
        issue19_ttm_path.read_text(encoding="utf-8")
    )
    v1 = pd.read_csv(ranked_v1, low_memory=False)
    v2 = pd.read_csv(ranked_v2, low_memory=False)

    store = BacktestPriceStore(prices)
    benchmark_store = BacktestPriceStore(
        benchmark_prices,
        ticker_column="ticker",
    )

    specs = _model_specs()
    frames = {"v1": v1, "v2": v2}
    results = {}
    summary_rows = []
    weekly_frames = []
    trade_frames = []

    print("ISSUE #8 PRIMARY ALLOCATION COMPARISON")
    print("Running frozen model/rule combinations...", flush=True)

    for model_name in ("v1", "v2"):
        frame = frames[model_name]
        spec = specs[model_name]
        for rule in RULES:
            key = (model_name, rule.rule_id)
            print(
                f"  {model_name.upper():2s} / {rule.rule_id}",
                flush=True,
            )
            result = run_allocation_backtest(
                frame,
                price_store=store,
                model_id=f"{model_name}_{rule.rule_id}",
                score_column=spec["score_column"],
                config=AllocationBacktestConfig(
                    weekly_contribution=(
                        ALLOCATION_EXPERIMENT.weekly_contribution
                    ),
                    top_n=ALLOCATION_EXPERIMENT.top_n,
                    selection_flag=spec["eligible_column"],
                    max_addon_position_weight=(
                        ALLOCATION_EXPERIMENT.max_addon_position_weight
                    ),
                    rule_id=rule.rule_id,
                ),
                start=date(
                    ALLOCATION_EXPERIMENT.evaluation_start_year,
                    1,
                    1,
                ),
                end=date(args.end_year, 12, 31),
            )
            results[key] = result
            summary_rows.append(dict(result.summary))
            weekly_frames.append(result.weekly)
            trade_frames.append(result.trades)

    # Equal-dollar must reproduce the already-validated Issue #19 evidence.
    equal_v1 = results[("v1", "equal_dollar")]
    equal_v2 = results[("v2", "equal_dollar")]
    _require_close(
        equal_v1.summary["xirr"],
        issue19_ttm["candidate_v1_xirr"],
        label="equal-dollar V1 XIRR parity",
    )
    _require_close(
        equal_v2.summary["xirr"],
        issue19_ttm["candidate_v2_xirr"],
        label="equal-dollar V2 XIRR parity",
    )
    _require_close(
        equal_v1.summary["max_drawdown"],
        issue19_ttm["candidate_v1_max_drawdown"],
        label="equal-dollar V1 drawdown parity",
    )
    _require_close(
        equal_v2.summary["max_drawdown"],
        issue19_ttm["candidate_v2_max_drawdown"],
        label="equal-dollar V2 drawdown parity",
    )

    common_dates = sorted(
        set(equal_v1.weekly["decision_date"])
        & set(equal_v2.weekly["decision_date"])
    )
    benchmark = _benchmark(
        price_store=benchmark_store,
        symbol=args.benchmark_symbol.upper(),
        decision_dates=common_dates,
    )
    benchmark_xirr = float(benchmark.summary["xirr"])

    summary = pd.DataFrame(summary_rows)
    summary["benchmark_symbol"] = args.benchmark_symbol.upper()
    summary["benchmark_xirr"] = benchmark_xirr
    summary["xirr_vs_benchmark"] = (
        pd.to_numeric(summary["xirr"], errors="coerce")
        - benchmark_xirr
    )

    equal_by_model = (
        summary.loc[summary["allocation_rule"].eq("equal_dollar")]
        .set_index("model_id")
    )
    baseline_xirr = {
        "v1": float(
            summary.loc[
                summary["model_id"].eq("v1_equal_dollar"),
                "xirr",
            ].iloc[0]
        ),
        "v2": float(
            summary.loc[
                summary["model_id"].eq("v2_equal_dollar"),
                "xirr",
            ].iloc[0]
        ),
    }
    baseline_dd = {
        "v1": float(
            summary.loc[
                summary["model_id"].eq("v1_equal_dollar"),
                "max_drawdown",
            ].iloc[0]
        ),
        "v2": float(
            summary.loc[
                summary["model_id"].eq("v2_equal_dollar"),
                "max_drawdown",
            ].iloc[0]
        ),
    }
    summary["model_variant"] = (
        summary["model_id"].astype(str).str.split("_").str[0]
    )
    summary["xirr_delta_vs_equal"] = summary.apply(
        lambda row: (
            float(row["xirr"])
            - baseline_xirr[str(row["model_variant"])]
        ),
        axis=1,
    )
    summary["drawdown_delta_vs_equal"] = summary.apply(
        lambda row: (
            float(row["max_drawdown"])
            - baseline_dd[str(row["model_variant"])]
        ),
        axis=1,
    )

    # This is descriptive evidence only; no allocation winner is selected here.
    output_dir.mkdir(parents=True)
    summary.to_csv(
        output_dir / "primary_summary.csv",
        index=False,
    )
    pd.concat(weekly_frames, ignore_index=True).to_csv(
        output_dir / "primary_weekly.csv",
        index=False,
    )
    pd.concat(trade_frames, ignore_index=True).to_csv(
        output_dir / "primary_trades.csv",
        index=False,
    )
    benchmark.weekly.to_csv(
        output_dir / "benchmark_weekly.csv",
        index=False,
    )
    benchmark.trades.to_csv(
        output_dir / "benchmark_trades.csv",
        index=False,
    )

    result_manifest = {
        "schema_version": 1,
        "status": "ISSUE8_PRIMARY_COMPARISON_COMPLETE",
        "experiment_id": ALLOCATION_EXPERIMENT.experiment_id,
        "equal_dollar_parity_passed": True,
        "benchmark": args.benchmark_symbol.upper(),
        "benchmark_xirr": benchmark_xirr,
        "result_rows": len(summary),
        "models": ["v1", "v2"],
        "allocation_rules": [rule.rule_id for rule in RULES],
        "interpretation": (
            "descriptive primary comparison only; no winner selected and no "
            "live allocation change authorized"
        ),
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
        "inputs": fingerprint_files(
            root=root,
            paths=[
                freeze_manifest_path,
                issue19_state_path,
                issue19_ttm_path,
                ranked_v1,
                ranked_v2,
                prices,
                benchmark_prices,
            ],
        ),
        "code": provenance,
    }
    (output_dir / "result_manifest.json").write_text(
        json.dumps(result_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("EQUAL-DOLLAR PARITY: PASS")
    print(f"Benchmark {args.benchmark_symbol.upper()} XIRR: {benchmark_xirr:.2%}")
    print()
    print("PRIMARY RESULTS")
    display_cols = [
        "model_variant",
        "allocation_rule",
        "xirr",
        "xirr_delta_vs_equal",
        "annualized_time_weighted_return",
        "max_drawdown",
        "drawdown_delta_vs_equal",
        "mean_portfolio_hhi",
        "max_largest_position_weight",
        "mean_cash_pct",
        "mean_allocation_turnover",
        "mean_contribution_hhi",
        "mean_effective_contribution_names",
        "xirr_vs_benchmark",
    ]
    for row in summary[display_cols].itertuples(index=False):
        print(
            f"{row.model_variant.upper()} {row.allocation_rule:<18} "
            f"XIRR={row.xirr:7.2%} "
            f"dEq={row.xirr_delta_vs_equal:+7.2%} "
            f"TWRann={row.annualized_time_weighted_return:7.2%} "
            f"DD={row.max_drawdown:7.2%} "
            f"HHI={row.mean_portfolio_hhi:.4f} "
            f"MaxPos={row.max_largest_position_weight:6.2%} "
            f"Cash={row.mean_cash_pct:6.2%} "
            f"AllocTO={row.mean_allocation_turnover:6.2%} "
            f"ContribHHI={row.mean_contribution_hhi:.4f} "
            f"EffN={row.mean_effective_contribution_names:5.2f} "
            f"vsVOO={row.xirr_vs_benchmark:+7.2%}"
        )
    print()
    print(f"Output directory:             {output_dir}")
    print("NO ALLOCATION WINNER WAS SELECTED.")
    print("NO LIVE, BROKER, OR ORDER RULE WAS CHANGED.")


if __name__ == "__main__":
    main()
