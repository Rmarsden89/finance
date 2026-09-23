from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_challenger import add_long_growth_v2_ttm_scores
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)
from finance.research.v2_impact import score_long_growth_panel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the frozen full V2 TTM valuation challenger from the "
            "historical research panel. No backtest or execution capability."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _normalize_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["decision_date"] = pd.to_datetime(
        result["decision_date"], errors="raise"
    ).dt.normalize()
    result["ticker"] = result["ticker"].astype(str).str.upper()
    return result


def _rank_compare(
    v1: pd.DataFrame,
    v2: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    keys = ["decision_date", "ticker"]
    merged = v1[
        keys + ["long_growth_v1_score", "top_conviction_eligible"]
    ].merge(
        v2[
            keys
            + [
                f"{TTM_CHALLENGER.model_id}_score",
                "v2_top_conviction_eligible",
            ]
        ],
        on=keys,
        how="inner",
        validate="one_to_one",
    )

    detail_frames = []
    weekly_rows = []
    for decision_date, group in merged.groupby("decision_date", sort=True):
        group = group.copy()
        v1_ok = (
            group["top_conviction_eligible"].fillna(False).astype(bool)
            & pd.to_numeric(group["long_growth_v1_score"], errors="coerce").notna()
        )
        v2_ok = (
            group["v2_top_conviction_eligible"].fillna(False).astype(bool)
            & pd.to_numeric(
                group[f"{TTM_CHALLENGER.model_id}_score"], errors="coerce"
            ).notna()
        )
        group["v1_rank"] = pd.to_numeric(
            group["long_growth_v1_score"], errors="coerce"
        ).where(v1_ok).rank(method="min", ascending=False)
        group["v2_rank"] = pd.to_numeric(
            group[f"{TTM_CHALLENGER.model_id}_score"], errors="coerce"
        ).where(v2_ok).rank(method="min", ascending=False)
        overlap = group["v1_rank"].notna() & group["v2_rank"].notna()
        group["absolute_rank_change"] = (
            group["v2_rank"] - group["v1_rank"]
        ).abs().where(overlap)

        v1_top = set(group.loc[group["v1_rank"].le(10), "ticker"])
        v2_top = set(group.loc[group["v2_rank"].le(10), "ticker"])
        weekly_rows.append({
            "decision_date": decision_date,
            "v1_top10_count": len(v1_top),
            "v2_top10_count": len(v2_top),
            "top10_overlap": len(v1_top & v2_top),
            "median_absolute_rank_change": (
                float(group.loc[overlap, "absolute_rank_change"].median())
                if overlap.any() else None
            ),
        })
        detail_frames.append(group)

    return pd.concat(detail_frames, ignore_index=True), pd.DataFrame(weekly_rows)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2_paths = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm_paths = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    manifest = json.loads(v2_paths["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "SEC_RESEARCH_COMPLETE":
        raise SystemExit("V2 SEC research must be complete")
    if any(bool(v) for v in manifest.get("execution_capabilities", {}).values()):
        raise SystemExit("V2 manifest enables an execution capability")

    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("Manifest lacks historical panel")
    historical_path = Path(historical_value)
    if not historical_path.is_absolute():
        historical_path = root / historical_path

    factor_path = ttm_paths["ttm_history_factor_panel"]
    history_summary_path = ttm_paths["ttm_history_summary"]
    required = {
        "historical panel": historical_path,
        "historical TTM factor panel": factor_path,
        "historical TTM replay summary": history_summary_path,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit("Missing input(s):\n  " + "\n  ".join(missing))

    if ttm_paths["ttm_challenger_dir"].exists():
        raise SystemExit(
            "Full challenger output already exists; preserve or rename it first: "
            f"{ttm_paths['ttm_challenger_dir']}"
        )

    history_summary = json.loads(history_summary_path.read_text(encoding="utf-8"))
    if history_summary.get("status") != "TTM_HISTORICAL_VALUATION_REPLAY_COMPLETE":
        raise SystemExit("Historical TTM replay is not complete")
    if int(history_summary.get("pit_violations", 1)) != 0:
        raise SystemExit("Historical TTM replay has PIT violations")

    print("V2 FULL TTM CHALLENGER BUILD")
    panel = pd.read_csv(historical_path, low_memory=False)
    ttm = pd.read_csv(factor_path, low_memory=False)
    panel = _normalize_keys(panel)
    ttm = _normalize_keys(ttm)

    print("Scoring exact V1 champion from frozen historical panel...", flush=True)
    v1 = score_long_growth_panel(panel)
    v1 = _normalize_keys(v1)

    ttm_cols = [
        "decision_date", "ticker", "cik",
        "ttm_valuation_score", "ttm_valuation_eligible",
        "ttm_valuation_factor_count",
    ]
    available = [c for c in ttm_cols if c in ttm.columns]
    merged = v1.merge(
        ttm[available],
        on=["decision_date", "ticker", "cik"],
        how="left",
        validate="one_to_one",
    )

    # The V2 composite begins with the exact V1 family scores and substitutes
    # only the TTM valuation family.
    v2 = add_long_growth_v2_ttm_scores(merged)

    # Fail closed if any non-valuation family changed during construction.
    for family in ("quality", "financial_health", "growth"):
        left = pd.to_numeric(v1[f"{family}_score"], errors="coerce")
        right = pd.to_numeric(v2[f"{family}_score"], errors="coerce")
        equal = left.eq(right) | (left.isna() & right.isna())
        if not bool(equal.all()):
            raise SystemExit(f"Non-valuation family changed: {family}")

    rank_detail, weekly = _rank_compare(v1, v2)

    summary = {
        "schema_version": 1,
        "status": "TTM_FULL_CHALLENGER_BUILD_COMPLETE",
        "model_id": TTM_CHALLENGER.model_id,
        "as_of": args.as_of.isoformat(),
        "historical_rows": len(panel),
        "decision_dates": int(panel["decision_date"].nunique()),
        "v1_top_conviction_rows": int(
            v1["top_conviction_eligible"].fillna(False).astype(bool).sum()
        ),
        "v2_top_conviction_rows": int(
            v2["v2_top_conviction_eligible"].fillna(False).astype(bool).sum()
        ),
        "median_weekly_top10_overlap": float(weekly["top10_overlap"].median()),
        "mean_weekly_top10_overlap": float(weekly["top10_overlap"].mean()),
        "median_weekly_absolute_rank_change": float(
            weekly["median_absolute_rank_change"].dropna().median()
        ),
        "non_valuation_family_changes": 0,
        "backtest_run": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    out = ttm_paths["ttm_challenger_dir"]
    out.mkdir(parents=True, exist_ok=False)

    v1.to_csv(ttm_paths["ttm_challenger_v1_panel"], index=False)
    v2.to_csv(ttm_paths["ttm_challenger_v2_panel"], index=False)
    rank_detail.to_csv(ttm_paths["ttm_challenger_rank_comparison"], index=False)
    weekly.to_csv(ttm_paths["ttm_challenger_weekly_summary"], index=False)
    ttm_paths["ttm_challenger_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ttm_paths["ttm_challenger_fingerprints"].write_text(
        json.dumps({
            "schema_version": 1,
            "inputs": fingerprint_files(root=root, paths=list(required.values())),
            "code": git_provenance(root),
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 FULL TTM CHALLENGER BUILD COMPLETE")
    print(f"Rows:                       {len(panel):,}")
    print(f"Decision dates:             {panel['decision_date'].nunique():,}")
    print(f"V1 Top-Conviction rows:     {summary['v1_top_conviction_rows']:,}")
    print(f"V2 Top-Conviction rows:     {summary['v2_top_conviction_rows']:,}")
    print(
        f"Median / mean Top10 overlap: "
        f"{summary['median_weekly_top10_overlap']:.1f} / "
        f"{summary['mean_weekly_top10_overlap']:.2f}"
    )
    print(
        f"Median weekly rank shift:   "
        f"{summary['median_weekly_absolute_rank_change']:.1f}"
    )
    print(f"Output directory:           {out}")
    print("NO BACKTEST OR PERFORMANCE RESULT WAS GENERATED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
