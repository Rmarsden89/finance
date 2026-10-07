from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.factors.registry import FACTOR_REGISTRY
from finance.scoring.family_scores import FAMILY_DEFINITIONS


RANK_BAND_ORDER = ("top10", "rank11_25", "rank26_50", "rank51_plus", "unranked")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize V5 historical attribution dataset coverage without evaluating performance."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/v5/attribution_dataset/coverage"),
    )
    parser.add_argument("--horizons-weeks", default="1,4,13,26,52")
    return parser.parse_args()


def _bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def build_factor_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for factor, definition in FACTOR_REGISTRY.items():
        raw = factor
        validated = f"{factor}_validated"
        score = f"{factor}_score"
        rows.append({
            "factor": factor,
            "family": definition.family,
            "direction": definition.direction,
            "lookback_weeks": definition.lookback_weeks,
            "rows": len(frame),
            "raw_available": int(frame[raw].notna().sum()) if raw in frame.columns else 0,
            "validated_available": int(frame[validated].notna().sum()) if validated in frame.columns else 0,
            "score_available": int(frame[score].notna().sum()) if score in frame.columns else 0,
        })
    result = pd.DataFrame(rows)
    for column in ("raw_available", "validated_available", "score_available"):
        result[column + "_pct"] = result[column] / result["rows"]
    return result


def build_family_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for family in FAMILY_DEFINITIONS:
        score_col = f"{family}_score"
        eligible_col = f"{family}_eligible"
        count_col = f"{family}_factor_count"
        weight_col = f"{family}_weight_coverage"
        rows.append({
            "family": family,
            "rows": len(frame),
            "score_available": int(frame[score_col].notna().sum()),
            "eligible_rows": int(_bool(frame[eligible_col]).sum()),
            "mean_factor_count": float(pd.to_numeric(frame[count_col], errors="coerce").mean()),
            "mean_weight_coverage": float(pd.to_numeric(frame[weight_col], errors="coerce").mean()),
        })
    result = pd.DataFrame(rows)
    result["score_available_pct"] = result["score_available"] / result["rows"]
    result["eligible_pct"] = result["eligible_rows"] / result["rows"]
    return result


def build_rank_band_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    band = frame["v1_rank_band"].astype(str)
    for label in RANK_BAND_ORDER:
        subset = frame.loc[band.eq(label)]
        if subset.empty:
            continue
        row = {
            "rank_band": label,
            "rows": len(subset),
            "decision_dates": int(pd.to_datetime(subset["decision_date"]).nunique()),
        }
        for family in FAMILY_DEFINITIONS:
            row[f"{family}_score_available_pct"] = float(subset[f"{family}_score"].notna().mean())
            row[f"{family}_eligible_pct"] = float(_bool(subset[f"{family}_eligible"]).mean())
        rows.append(row)
    return pd.DataFrame(rows)


def build_horizon_coverage(frame: pd.DataFrame, horizons: tuple[int, ...]) -> pd.DataFrame:
    rows = []
    band = frame["v1_rank_band"].astype(str)
    for weeks in horizons:
        status_col = f"fwd_{weeks}w_status"
        status = frame[status_col].astype(str)
        for label in ("all",) + RANK_BAND_ORDER:
            mask = pd.Series(True, index=frame.index) if label == "all" else band.eq(label)
            subset = status.loc[mask]
            if subset.empty:
                continue
            rows.append({
                "horizon_weeks": weeks,
                "rank_band": label,
                "rows": int(len(subset)),
                "mature_rows": int(subset.eq("mature").sum()),
                "pending_rows": int(subset.eq("pending").sum()),
                "missing_entry_rows": int(subset.eq("missing_entry_price").sum()),
                "missing_exit_rows": int(subset.eq("missing_exit_price").sum()),
                "missing_spy_entry_rows": int(subset.eq("missing_spy_entry").sum()),
                "missing_spy_exit_rows": int(subset.eq("missing_spy_exit").sum()),
            })
    result = pd.DataFrame(rows)
    result["mature_pct"] = result["mature_rows"] / result["rows"]
    return result


def main() -> None:
    args = parse_args()
    horizons = tuple(int(value.strip()) for value in args.horizons_weeks.split(",") if value.strip())
    frame = pd.read_csv(args.dataset, low_memory=False)

    required = {
        "decision_date", "ticker", "v1_rank_band", "top_conviction_eligible",
        "long_growth_v1_score",
    }
    required.update(f"{family}_score" for family in FAMILY_DEFINITIONS)
    required.update(f"{family}_eligible" for family in FAMILY_DEFINITIONS)
    required.update(f"{family}_factor_count" for family in FAMILY_DEFINITIONS)
    required.update(f"{family}_weight_coverage" for family in FAMILY_DEFINITIONS)
    required.update(f"fwd_{weeks}w_status" for weeks in horizons)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit("Coverage input missing columns: " + ", ".join(missing))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    factors = build_factor_coverage(frame)
    families = build_family_coverage(frame)
    rank_bands = build_rank_band_coverage(frame)
    horizons_df = build_horizon_coverage(frame, horizons)

    factors.to_csv(args.output_dir / "factor_coverage.csv", index=False)
    families.to_csv(args.output_dir / "family_coverage.csv", index=False)
    rank_bands.to_csv(args.output_dir / "rank_band_coverage.csv", index=False)
    horizons_df.to_csv(args.output_dir / "forward_horizon_coverage.csv", index=False)

    summary = {
        "schema_version": 1,
        "research_only": True,
        "performance_analysis": False,
        "rows": int(len(frame)),
        "decision_dates": int(pd.to_datetime(frame["decision_date"]).nunique()),
        "unique_tickers": int(frame["ticker"].astype(str).nunique()),
        "top_conviction_rows": int(_bool(frame["top_conviction_eligible"]).sum()),
        "ranked_rows": int(pd.to_numeric(frame.get("v1_rank"), errors="coerce").notna().sum()),
        "families": FAMILY_DEFINITIONS.keys(),
        "factor_count": len(FACTOR_REGISTRY),
        "horizons_weeks": list(horizons),
    }
    summary["families"] = list(summary["families"])
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print("V5 ATTRIBUTION COVERAGE REPORT")
    print("Research only:             YES")
    print("Performance analysis:      NO")
    print(f"Rows:                      {len(frame):,}")
    print(f"Decision dates:            {summary['decision_dates']:,}")
    print(f"Ranked rows:               {summary['ranked_rows']:,}")
    print()
    print("FAMILY SCORE COVERAGE")
    for row in families.itertuples(index=False):
        print(f"{row.family:20s} {row.score_available_pct:7.2%}  eligible={row.eligible_pct:7.2%}")
    print()
    print("MATURE FORWARD-RETURN COVERAGE")
    all_horizons = horizons_df.loc[horizons_df["rank_band"] == "all"]
    for row in all_horizons.itertuples(index=False):
        print(f"{int(row.horizon_weeks):>2}w: {int(row.mature_rows):,}/{int(row.rows):,} ({row.mature_pct:.2%})")
    print(f"Output:                    {args.output_dir}")


if __name__ == "__main__":
    main()
