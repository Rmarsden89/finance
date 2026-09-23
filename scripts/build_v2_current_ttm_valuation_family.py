from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finance.factors.validation import validate_raw_factors
from finance.factors.valuation import add_valuation_factors
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.ttm_valuation_family import (
    ANNUAL_VALUATION_WEIGHTS,
    add_ttm_valuation_factors,
    add_ttm_valuation_family_score,
    normalize_ttm_valuation_factors,
    score_weighted_family,
)
from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)
from finance.scoring.normalize import normalize_validated_factors


PAIR_MAP = {
    "earnings_yield": ("earnings_yield_annual", "earnings_yield_ttm"),
    "sales_yield": ("sales_yield_annual", "sales_yield_ttm"),
    "free_cash_flow_yield": (
        "free_cash_flow_yield_annual",
        "free_cash_flow_yield_ttm",
    ),
    "book_to_market": ("book_to_market", "book_to_market"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build and compare the current-state V2 TTM valuation family "
            "against the frozen annual valuation convention. Research only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _distribution_rows(
    annual: pd.DataFrame,
    ttm: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for label, (annual_factor, ttm_factor) in PAIR_MAP.items():
        for variant, frame, factor in (
            ("annual", annual, annual_factor),
            ("ttm", ttm, ttm_factor),
        ):
            validated = (
                "book_to_market_validated"
                if label == "book_to_market"
                else f"{factor}_validated"
            )
            values = pd.to_numeric(frame.get(factor), errors="coerce")
            valid_values = pd.to_numeric(
                frame.get(validated), errors="coerce"
            )
            finite = valid_values[np.isfinite(valid_values)]
            rows.append(
                {
                    "factor_pair": label,
                    "variant": variant,
                    "rows": len(frame),
                    "raw_available": int(values.notna().sum()),
                    "validated_available": int(valid_values.notna().sum()),
                    "validated_mean": float(finite.mean()) if len(finite) else np.nan,
                    "validated_p05": float(finite.quantile(0.05)) if len(finite) else np.nan,
                    "validated_median": float(finite.median()) if len(finite) else np.nan,
                    "validated_p95": float(finite.quantile(0.95)) if len(finite) else np.nan,
                    "validated_min": float(finite.min()) if len(finite) else np.nan,
                    "validated_max": float(finite.max()) if len(finite) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _correlations(
    annual: pd.DataFrame,
    ttm: pd.DataFrame,
) -> pd.DataFrame:
    merged = annual[["ticker", "cik"]].merge(
        ttm[["ticker", "cik"]],
        on=["ticker", "cik"],
        how="inner",
        validate="one_to_one",
    )
    annual_idx = annual.set_index(["ticker", "cik"])
    ttm_idx = ttm.set_index(["ticker", "cik"])
    rows: list[dict[str, object]] = []

    for label, (annual_factor, ttm_factor) in PAIR_MAP.items():
        annual_valid = (
            "book_to_market_validated"
            if label == "book_to_market"
            else f"{annual_factor}_validated"
        )
        ttm_valid = (
            "book_to_market_validated"
            if label == "book_to_market"
            else f"{ttm_factor}_validated"
        )
        joined = pd.DataFrame(index=pd.MultiIndex.from_frame(merged))
        joined["annual_raw"] = pd.to_numeric(
            annual_idx[annual_factor], errors="coerce"
        )
        joined["ttm_raw"] = pd.to_numeric(
            ttm_idx[ttm_factor], errors="coerce"
        )
        joined["annual_validated"] = pd.to_numeric(
            annual_idx[annual_valid], errors="coerce"
        )
        joined["ttm_validated"] = pd.to_numeric(
            ttm_idx[ttm_valid], errors="coerce"
        )
        overlap = joined[["annual_validated", "ttm_validated"]].dropna()
        rows.append(
            {
                "factor_pair": label,
                "overlap_rows": len(overlap),
                "raw_pearson": joined[["annual_raw", "ttm_raw"]].corr(
                    method="pearson"
                ).iloc[0, 1],
                "validated_pearson": (
                    overlap.corr(method="pearson").iloc[0, 1]
                    if len(overlap) >= 2
                    else np.nan
                ),
                "validated_spearman": (
                    overlap.corr(method="spearman").iloc[0, 1]
                    if len(overlap) >= 2
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def _rank_comparison(
    annual: pd.DataFrame,
    ttm: pd.DataFrame,
) -> pd.DataFrame:
    left = annual[
        [
            "ticker",
            "cik",
            "annual_valuation_score",
            "annual_valuation_eligible",
            "annual_valuation_factor_count",
        ]
    ].copy()
    right = ttm[
        [
            "ticker",
            "cik",
            "ttm_valuation_score",
            "ttm_valuation_eligible",
            "ttm_valuation_factor_count",
        ]
    ].copy()
    result = left.merge(
        right, on=["ticker", "cik"], how="outer", validate="one_to_one"
    )
    result["annual_rank"] = pd.to_numeric(
        result["annual_valuation_score"], errors="coerce"
    ).rank(method="min", ascending=False, na_option="keep")
    result["ttm_rank"] = pd.to_numeric(
        result["ttm_valuation_score"], errors="coerce"
    ).rank(method="min", ascending=False, na_option="keep")
    overlap = result["annual_rank"].notna() & result["ttm_rank"].notna()
    result["rank_change_ttm_minus_annual"] = (
        result["ttm_rank"] - result["annual_rank"]
    ).where(overlap)
    result["absolute_rank_change"] = (
        result["rank_change_ttm_minus_annual"].abs()
    )
    return result.sort_values(
        ["ttm_rank", "annual_rank", "ticker"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)


def _top10_comparison(rank: pd.DataFrame) -> pd.DataFrame:
    annual_top = rank.loc[
        rank["annual_rank"].le(10),
        ["ticker", "cik", "annual_rank", "annual_valuation_score"],
    ].copy()
    ttm_top = rank.loc[
        rank["ttm_rank"].le(10),
        ["ticker", "cik", "ttm_rank", "ttm_valuation_score"],
    ].copy()
    merged = annual_top.merge(
        ttm_top, on=["ticker", "cik"], how="outer"
    )
    merged["in_annual_top10"] = merged["annual_rank"].notna()
    merged["in_ttm_top10"] = merged["ttm_rank"].notna()
    merged["in_both"] = (
        merged["in_annual_top10"] & merged["in_ttm_top10"]
    )
    return merged.sort_values(
        ["in_both", "ttm_rank", "annual_rank", "ticker"],
        ascending=[False, True, True, True],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)

    required = {
        "current V2 snapshot": v2["current_snapshot"],
        "current TTM numerators": ttm["ttm_current_numerators"],
        "latest TTM by concept": ttm["ttm_latest_by_concept"],
        "TTM validation summary": ttm["ttm_validation_summary"],
        "Q4 difference summary": ttm["ttm_q4_difference_summary"],
        "V2 research manifest": v2["manifest"],
    }
    missing = [
        f"{name}: {path}" for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing required current TTM valuation input(s):\n  "
            + "\n  ".join(missing)
        )

    validation = json.loads(
        ttm["ttm_validation_summary"].read_text(encoding="utf-8")
    )
    if validation.get("status") != "CURRENT_TTM_NUMERATOR_VALIDATION_COMPLETE":
        raise SystemExit("Current TTM numerator validation is not complete")
    if validation.get("income_quarter_policy") != "ytd_preferred":
        raise SystemExit("Current TTM valuation requires ytd_preferred inputs")
    if int(validation.get("pit_violations", 1)) != 0:
        raise SystemExit("Current TTM numerator validation has PIT violations")

    q4 = json.loads(
        ttm["ttm_q4_difference_summary"].read_text(encoding="utf-8")
    )
    if q4.get("status") != "TTM_Q4_DIFFERENCE_DIAGNOSTIC_COMPLETE":
        raise SystemExit("Q4 difference diagnostic is not complete")

    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    if any(
        bool(value)
        for value in manifest.get("execution_capabilities", {}).values()
    ):
        raise SystemExit("V2 manifest enables an execution capability")

    if ttm["ttm_valuation_family_dir"].exists():
        raise SystemExit(
            "Current TTM valuation-family output already exists; preserve "
            f"or rename it before rerun: {ttm['ttm_valuation_family_dir']}"
        )

    print("V2 CURRENT TTM VALUATION FAMILY")
    print(f"Decision date:              {args.as_of.isoformat()}")
    print("Loading current annual and TTM inputs...", flush=True)

    snapshot = pd.read_csv(v2["current_snapshot"], low_memory=False)
    current_ttm = pd.read_csv(ttm["ttm_current_numerators"], low_memory=False)
    latest_ttm = pd.read_csv(ttm["ttm_latest_by_concept"], low_memory=False)

    print("Scoring annual valuation baseline with frozen V1 conventions...", flush=True)
    annual = add_valuation_factors(snapshot)
    annual = validate_raw_factors(annual)
    annual = normalize_validated_factors(annual)
    annual = score_weighted_family(
        annual,
        weights=ANNUAL_VALUATION_WEIGHTS,
        output_prefix="annual_valuation",
        minimum_factors=2,
    )

    print("Building isolated TTM valuation factors...", flush=True)
    ttm_frame = add_ttm_valuation_factors(
        snapshot,
        current_ttm,
        latest_ttm,
    )

    # book_to_market is intentionally unchanged: reuse the exact annual/V1
    # raw, validated, normalized, and score columns.
    book_cols = [
        "ticker",
        "cik",
        "book_to_market",
        "book_to_market_valid",
        "book_to_market_invalid_reason",
        "book_to_market_validated",
        "book_to_market_winsorized",
        "book_to_market_winsorized_flag",
        "book_to_market_percentile",
        "book_to_market_score",
    ]
    ttm_frame = ttm_frame.drop(
        columns=[c for c in book_cols[2:] if c in ttm_frame.columns],
        errors="ignore",
    ).merge(
        annual[[c for c in book_cols if c in annual.columns]],
        on=["ticker", "cik"],
        how="left",
        validate="one_to_one",
    )
    ttm_frame = normalize_ttm_valuation_factors(ttm_frame)
    ttm_frame = add_ttm_valuation_family_score(ttm_frame)

    print("Building current factor/rank comparison...", flush=True)
    distributions = _distribution_rows(annual, ttm_frame)
    correlations = _correlations(annual, ttm_frame)
    rank = _rank_comparison(annual, ttm_frame)
    top10 = _top10_comparison(rank)

    annual_eligible = int(annual["annual_valuation_eligible"].sum())
    ttm_eligible = int(ttm_frame["ttm_valuation_eligible"].sum())
    joined_elig = annual[
        ["ticker", "cik", "annual_valuation_eligible"]
    ].merge(
        ttm_frame[["ticker", "cik", "ttm_valuation_eligible"]],
        on=["ticker", "cik"],
        how="inner",
    )
    newly = int(
        (
            ~joined_elig["annual_valuation_eligible"]
            & joined_elig["ttm_valuation_eligible"]
        ).sum()
    )
    lost = int(
        (
            joined_elig["annual_valuation_eligible"]
            & ~joined_elig["ttm_valuation_eligible"]
        ).sum()
    )

    score_pair = rank[
        ["annual_valuation_score", "ttm_valuation_score"]
    ].dropna()
    family_pearson = (
        float(score_pair.corr(method="pearson").iloc[0, 1])
        if len(score_pair) >= 2 else np.nan
    )
    family_spearman = (
        float(score_pair.corr(method="spearman").iloc[0, 1])
        if len(score_pair) >= 2 else np.nan
    )
    rank_overlap = rank["absolute_rank_change"].dropna()
    top10_overlap = int(top10["in_both"].sum())

    family_comparison = pd.DataFrame([
        {
            "rows": len(snapshot),
            "annual_valuation_eligible": annual_eligible,
            "ttm_valuation_eligible": ttm_eligible,
            "newly_eligible_ttm": newly,
            "lost_eligible_ttm": lost,
            "overlapping_scored_rows": len(score_pair),
            "family_score_pearson": family_pearson,
            "family_score_spearman": family_spearman,
            "median_absolute_rank_change": (
                float(rank_overlap.median()) if len(rank_overlap) else np.nan
            ),
            "p90_absolute_rank_change": (
                float(rank_overlap.quantile(0.90))
                if len(rank_overlap) else np.nan
            ),
            "valuation_family_top10_overlap": top10_overlap,
        }
    ])

    detail_cols = [
        "ticker", "cik", "company_name", "decision_date", "close",
        "shares_outstanding", "market_cap",
        "earnings_yield_annual", "earnings_yield_annual_validated",
        "earnings_yield_annual_score",
        "sales_yield_annual", "sales_yield_annual_validated",
        "sales_yield_annual_score",
        "free_cash_flow_yield_annual",
        "free_cash_flow_yield_annual_validated",
        "free_cash_flow_yield_annual_score",
        "book_to_market", "book_to_market_validated", "book_to_market_score",
        "annual_valuation_factor_count", "annual_valuation_eligible",
        "annual_valuation_score",
    ]
    annual_detail = annual[
        [c for c in detail_cols if c in annual.columns]
    ].copy()
    ttm_cols = [
        "ticker", "cik", "ttm_net_income", "ttm_revenue",
        "ttm_free_cash_flow", "market_cap_ttm",
        "earnings_yield_ttm", "earnings_yield_ttm_validated",
        "earnings_yield_ttm_invalid_reason", "earnings_yield_ttm_score",
        "sales_yield_ttm", "sales_yield_ttm_validated",
        "sales_yield_ttm_invalid_reason", "sales_yield_ttm_score",
        "free_cash_flow_yield_ttm",
        "free_cash_flow_yield_ttm_validated",
        "free_cash_flow_yield_ttm_invalid_reason",
        "free_cash_flow_yield_ttm_score",
        "ttm_valuation_factor_count", "ttm_valuation_eligible",
        "ttm_valuation_score",
    ]
    factor_detail = annual_detail.merge(
        ttm_frame[[c for c in ttm_cols if c in ttm_frame.columns]],
        on=["ticker", "cik"],
        how="left",
        validate="one_to_one",
    )

    summary = {
        "schema_version": 1,
        "status": "CURRENT_TTM_VALUATION_FAMILY_COMPLETE",
        "as_of": args.as_of.isoformat(),
        "model_scope": "valuation_family_only",
        "income_quarter_policy": "ytd_preferred",
        "annual_valuation_eligible": annual_eligible,
        "ttm_valuation_eligible": ttm_eligible,
        "newly_eligible_ttm": newly,
        "lost_eligible_ttm": lost,
        "overlapping_scored_rows": len(score_pair),
        "family_score_pearson": family_pearson,
        "family_score_spearman": family_spearman,
        "median_absolute_rank_change": (
            float(rank_overlap.median()) if len(rank_overlap) else None
        ),
        "valuation_family_top10_overlap": top10_overlap,
        "q4_material_difference_rows": int(
            q4.get("material_difference_rows", -1)
        ),
        "book_to_market_policy": "reuse_frozen_annual_v1_score",
        "full_model_score_built": False,
        "v1_factor_values_modified": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    ttm["ttm_valuation_family_dir"].mkdir(parents=True, exist_ok=False)
    factor_detail.to_csv(ttm["ttm_valuation_factor_detail"], index=False)
    distributions.to_csv(
        ttm["ttm_valuation_distribution_summary"], index=False
    )
    family_comparison.to_csv(
        ttm["ttm_valuation_family_comparison"], index=False
    )
    rank.to_csv(ttm["ttm_valuation_rank_comparison"], index=False)
    correlations.to_csv(ttm["ttm_valuation_correlations"], index=False)
    top10.to_csv(ttm["ttm_valuation_top10"], index=False)
    ttm["ttm_valuation_summary"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    provenance = {
        "schema_version": 1,
        "decision_date": args.as_of.isoformat(),
        "direct_inputs": fingerprint_files(
            root=root, paths=list(required.values())
        ),
        "code": git_provenance(root),
    }
    ttm["ttm_valuation_fingerprints"].write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V2 CURRENT TTM VALUATION FAMILY COMPLETE")
    print(f"Annual valuation eligible: {annual_eligible:,}/{len(snapshot):,}")
    print(f"TTM valuation eligible:    {ttm_eligible:,}/{len(snapshot):,}")
    print(f"Newly / lost eligible:     {newly:,} / {lost:,}")
    print(f"Score overlap:              {len(score_pair):,}")
    print(f"Family Pearson/Spearman:    {family_pearson:.4f} / {family_spearman:.4f}")
    print(
        "Median abs rank change:    "
        f"{rank_overlap.median() if len(rank_overlap) else float('nan'):.1f}"
    )
    print(f"Valuation-family Top10:     {top10_overlap}/10 overlap")
    print(f"Output directory:           {ttm['ttm_valuation_family_dir']}")
    print("NO FULL V2 MODEL SCORE WAS BUILT.")
    print("V1 FACTOR VALUES AND SCORES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
