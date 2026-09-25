from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Decompose V3 liabilities rank movement into insertion pressure from "
            "newly eligible names versus score/rank reordering among names that "
            "were already Top-Conviction eligible. V3 research only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _rank(frame: pd.DataFrame, score_col: str) -> pd.Series:
    eligible = frame.loc[
        frame[score_col].notna()
    ].sort_values(
        [score_col, "ticker"],
        ascending=[False, True],
        kind="mergesort",
    )
    ranks = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    ranks.loc[eligible.index] = range(1, len(eligible) + 1)
    return ranks


def _summary_stats(series: pd.Series) -> dict[str, float | int | None]:
    values = pd.to_numeric(series, errors="coerce").dropna().abs()
    if values.empty:
        return {
            "rows": 0,
            "median_absolute_shift": None,
            "mean_absolute_shift": None,
            "max_absolute_shift": None,
            "moved_gt_5": 0,
            "moved_gt_10": 0,
            "moved_gt_25": 0,
        }
    return {
        "rows": int(len(values)),
        "median_absolute_shift": float(values.median()),
        "mean_absolute_shift": float(values.mean()),
        "max_absolute_shift": float(values.max()),
        "moved_gt_5": int(values.gt(5).sum()),
        "moved_gt_10": int(values.gt(10).sum()),
        "moved_gt_25": int(values.gt(25).sum()),
    }


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / "v3_liabilities_impact"
        / "scoring_experiment"
    )
    source = base / "universe_impact.csv"
    if not source.exists():
        raise SystemExit(f"Missing V3 scoring impact universe: {source}")

    frame = pd.read_csv(source, low_memory=False)
    required = {
        "ticker",
        "baseline_long_growth_v1_score",
        "v3_long_growth_v1_score",
        "baseline_top_conviction_eligible",
        "v3_top_conviction_eligible",
        "baseline_selection_rank",
        "v3_selection_rank",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(
            "Universe impact missing required columns: " + ", ".join(missing)
        )

    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    baseline_score = pd.to_numeric(
        frame["baseline_long_growth_v1_score"], errors="coerce"
    )
    v3_score = pd.to_numeric(
        frame["v3_long_growth_v1_score"], errors="coerce"
    )
    baseline_eligible = (
        frame["baseline_top_conviction_eligible"].fillna(False).astype(bool)
    )
    v3_eligible = frame["v3_top_conviction_eligible"].fillna(False).astype(bool)

    continuous = baseline_eligible & v3_eligible
    gained = ~baseline_eligible & v3_eligible

    # Counterfactual 1: insertion-only.
    # Existing eligible names retain baseline scores. Newly eligible names enter
    # with their V3 scores. Any movement among the existing population is caused
    # only by insertion of the new names.
    insertion = frame.loc[baseline_eligible | gained, ["ticker"]].copy()
    insertion["score"] = pd.NA
    insertion.loc[baseline_eligible.loc[insertion.index], "score"] = (
        baseline_score.loc[insertion.index]
    )
    insertion.loc[gained.loc[insertion.index], "score"] = (
        v3_score.loc[insertion.index]
    )
    insertion["score"] = pd.to_numeric(insertion["score"], errors="coerce")
    insertion["insertion_only_rank"] = _rank(insertion, "score")

    # Counterfactual 2: normalization/reordering-only.
    # Keep only names that were already eligible, but rank them with their V3
    # scores. Movement is therefore due to changed normalized scores/order, not
    # to insertion of newly eligible names.
    normalization = frame.loc[continuous, ["ticker"]].copy()
    normalization["score"] = v3_score.loc[normalization.index]
    normalization["normalization_only_rank"] = _rank(
        normalization, "score"
    )

    detail = frame.loc[continuous].copy()
    detail = detail.merge(
        insertion[["ticker", "insertion_only_rank"]],
        on="ticker",
        how="left",
        validate="one_to_one",
    ).merge(
        normalization[["ticker", "normalization_only_rank"]],
        on="ticker",
        how="left",
        validate="one_to_one",
    )

    detail["baseline_rank"] = pd.to_numeric(
        detail["baseline_selection_rank"], errors="coerce"
    )
    detail["actual_v3_rank"] = pd.to_numeric(
        detail["v3_selection_rank"], errors="coerce"
    )
    detail["insertion_only_rank"] = pd.to_numeric(
        detail["insertion_only_rank"], errors="coerce"
    )
    detail["normalization_only_rank"] = pd.to_numeric(
        detail["normalization_only_rank"], errors="coerce"
    )

    # Positive shift means the name moved down (worse rank number).
    detail["insertion_shift"] = (
        detail["insertion_only_rank"] - detail["baseline_rank"]
    )
    detail["normalization_shift"] = (
        detail["normalization_only_rank"] - detail["baseline_rank"]
    )
    detail["actual_shift"] = detail["actual_v3_rank"] - detail["baseline_rank"]
    detail["interaction_shift"] = (
        detail["actual_shift"]
        - detail["insertion_shift"]
        - detail["normalization_shift"]
    )

    detail["baseline_score"] = pd.to_numeric(
        detail["baseline_long_growth_v1_score"], errors="coerce"
    )
    detail["v3_score"] = pd.to_numeric(
        detail["v3_long_growth_v1_score"], errors="coerce"
    )
    detail["score_change"] = detail["v3_score"] - detail["baseline_score"]

    insertion_stats = _summary_stats(detail["insertion_shift"])
    normalization_stats = _summary_stats(detail["normalization_shift"])
    actual_stats = _summary_stats(detail["actual_shift"])
    interaction_stats = _summary_stats(detail["interaction_shift"])

    new_names = frame.loc[gained, [
        "ticker",
        "v3_long_growth_v1_score",
        "v3_selection_rank",
    ]].copy()
    new_names = new_names.sort_values(
        ["v3_selection_rank", "ticker"], kind="stable"
    )

    baseline_top10 = set(
        frame.loc[
            baseline_eligible
            & pd.to_numeric(
                frame["baseline_selection_rank"], errors="coerce"
            ).le(10),
            "ticker",
        ]
    )
    actual_top10 = set(
        frame.loc[
            v3_eligible
            & pd.to_numeric(
                frame["v3_selection_rank"], errors="coerce"
            ).le(10),
            "ticker",
        ]
    )
    insertion_top10 = set(
        insertion.loc[
            pd.to_numeric(
                insertion["insertion_only_rank"], errors="coerce"
            ).le(10),
            "ticker",
        ]
    )
    normalization_top10 = set(
        normalization.loc[
            pd.to_numeric(
                normalization["normalization_only_rank"], errors="coerce"
            ).le(10),
            "ticker",
        ]
    )

    summary = {
        "as_of": args.as_of.isoformat(),
        "continuous_top_conviction_names": int(continuous.sum()),
        "newly_top_conviction_eligible_names": int(gained.sum()),
        "insertion_only": insertion_stats,
        "normalization_only": normalization_stats,
        "actual_v3": actual_stats,
        "interaction": interaction_stats,
        "baseline_top10": sorted(baseline_top10),
        "insertion_only_top10_overlap": len(
            baseline_top10 & insertion_top10
        ),
        "normalization_only_top10_overlap": len(
            baseline_top10 & normalization_top10
        ),
        "actual_v3_top10_overlap": len(baseline_top10 & actual_top10),
        "insertion_only_top10_entered": sorted(
            insertion_top10 - baseline_top10
        ),
        "normalization_only_top10_entered": sorted(
            normalization_top10 - baseline_top10
        ),
        "actual_v3_top10_entered": sorted(actual_top10 - baseline_top10),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
    }

    detail_path = base / "rank_sensitivity_detail.csv"
    new_names_path = base / "newly_eligible_rank_pressure.csv"
    summary_path = base / "rank_sensitivity_summary.json"

    detail.sort_values(
        ["baseline_rank", "ticker"], kind="stable"
    ).to_csv(detail_path, index=False)
    new_names.to_csv(new_names_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 LIABILITIES NORMALIZATION / RANK SENSITIVITY")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Continuously eligible:       {summary['continuous_top_conviction_names']}")
    print(f"Newly eligible:              {summary['newly_top_conviction_eligible_names']}")
    print(
        "Insertion-only median/max: "
        f"{insertion_stats['median_absolute_shift']}/"
        f"{insertion_stats['max_absolute_shift']}"
    )
    print(
        "Normalization median/max:  "
        f"{normalization_stats['median_absolute_shift']}/"
        f"{normalization_stats['max_absolute_shift']}"
    )
    print(
        "Actual V3 median/max:       "
        f"{actual_stats['median_absolute_shift']}/"
        f"{actual_stats['max_absolute_shift']}"
    )
    print(
        "Moved >10 (insert/norm/actual): "
        f"{insertion_stats['moved_gt_10']}/"
        f"{normalization_stats['moved_gt_10']}/"
        f"{actual_stats['moved_gt_10']}"
    )
    print(
        "Top-10 overlap (insert/norm/actual): "
        f"{summary['insertion_only_top10_overlap']}/"
        f"{summary['normalization_only_top10_overlap']}/"
        f"{summary['actual_v3_top10_overlap']}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"Newly eligible:              {new_names_path}")
    print(f"Summary:                     {summary_path}")
    print("NO V1 OR V2 INPUTS, SCORES, OR RULES WERE MODIFIED.")


if __name__ == "__main__":
    main()
