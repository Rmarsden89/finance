from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure historical direct-liabilities overlap and mismatch behavior "
            "for the 32 current V3 SEC-native liabilities recovery candidates."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _load_candidates(base: Path) -> pd.DataFrame:
    paths = [
        base / "liabilities_identity_raw_strict" / "raw_sec_detail.csv",
        base / "liabilities_no_supported_current" / "raw_sec_detail.csv",
        base / "liabilities_alternate_tags" / "alternate_tag_detail.csv",
    ]
    frames = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"Missing current recovery detail: {path}")
        frame = pd.read_csv(path, low_memory=False)
        frame["source_file"] = path.name
        frames.append(frame)
    current = pd.concat(frames, ignore_index=True)
    current["ticker"] = current["ticker"].astype(str).str.upper().str.strip()
    current["cik"] = pd.to_numeric(current["cik"], errors="coerce").astype("Int64")
    current = current.loc[
        current["status"].astype(str).eq("current_plus_noncurrent_candidate")
    ].copy()
    current = current.drop_duplicates(["ticker", "cik"], keep="first")
    return current


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    overlap_path = (
        base
        / "liabilities_historical_validation"
        / "filing_overlap_validation.csv"
    )
    if not overlap_path.exists():
        raise SystemExit(f"Missing historical overlap validation: {overlap_path}")

    current = _load_candidates(base)
    overlap = pd.read_csv(overlap_path, low_memory=False)
    overlap["cik"] = pd.to_numeric(overlap["cik"], errors="coerce").astype("Int64")
    overlap["year"] = pd.to_datetime(
        overlap["ddate_date"], errors="coerce"
    ).dt.year

    scoped = overlap.loc[overlap["cik"].isin(set(current["cik"].dropna()))].copy()
    scoped = scoped.merge(
        current[["ticker", "cik"]],
        on="cik",
        how="left",
        validate="many_to_one",
    )

    per_ticker = (
        scoped.groupby(["ticker", "cik"], dropna=False)
        .agg(
            comparison_rows=("adsh", "size"),
            exact_matches=(
                "validation_band",
                lambda s: int(s.astype(str).eq("exact_match").sum()),
            ),
            within_0_01_pct=(
                "validation_band",
                lambda s: int(s.astype(str).eq("within_0_01_pct").sum()),
            ),
            within_0_1_pct=(
                "validation_band",
                lambda s: int(s.astype(str).eq("within_0_1_pct").sum()),
            ),
            within_1_pct=(
                "validation_band",
                lambda s: int(s.astype(str).eq("within_1_pct").sum()),
            ),
            material_differences=(
                "validation_band",
                lambda s: int(s.astype(str).eq("material_difference").sum()),
            ),
            first_year=("year", "min"),
            last_year=("year", "max"),
            max_absolute_relative_error=("absolute_relative_error", "max"),
        )
        .reset_index()
    )

    all_candidates = current[["ticker", "cik"]].merge(
        per_ticker,
        on=["ticker", "cik"],
        how="left",
    )
    count_cols = [
        "comparison_rows",
        "exact_matches",
        "within_0_01_pct",
        "within_0_1_pct",
        "within_1_pct",
        "material_differences",
    ]
    for col in count_cols:
        all_candidates[col] = (
            pd.to_numeric(all_candidates[col], errors="coerce")
            .fillna(0)
            .astype(int)
        )
    all_candidates["historical_control_overlap"] = all_candidates[
        "comparison_rows"
    ].gt(0)
    all_candidates["historically_clean"] = (
        all_candidates["comparison_rows"].gt(0)
        & all_candidates["material_differences"].eq(0)
    )
    all_candidates["historically_material"] = all_candidates[
        "material_differences"
    ].gt(0)

    summary = pd.DataFrame(
        [
            {
                "current_recovery_candidates": len(all_candidates),
                "with_historical_direct_overlap": int(
                    all_candidates["historical_control_overlap"].sum()
                ),
                "historically_clean_candidates": int(
                    all_candidates["historically_clean"].sum()
                ),
                "historically_material_candidates": int(
                    all_candidates["historically_material"].sum()
                ),
                "without_historical_direct_overlap": int(
                    (~all_candidates["historical_control_overlap"]).sum()
                ),
                "candidate_comparison_rows": int(
                    all_candidates["comparison_rows"].sum()
                ),
                "candidate_material_rows": int(
                    all_candidates["material_differences"].sum()
                ),
            }
        ]
    )

    output_dir = base / "liabilities_historical_validation"
    detail_path = output_dir / "current_candidate_historical_validation.csv"
    summary_path = output_dir / "current_candidate_historical_summary.csv"
    all_candidates.sort_values(
        ["historically_material", "comparison_rows", "ticker"],
        ascending=[False, False, True],
        kind="stable",
    ).to_csv(detail_path, index=False)
    summary.to_csv(summary_path, index=False)

    row = summary.iloc[0]
    print("V3 CURRENT LIABILITIES CANDIDATE HISTORICAL VALIDATION")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Current recovery candidates: {row['current_recovery_candidates']}")
    print(f"With historical overlap:     {row['with_historical_direct_overlap']}")
    print(f"Historically clean:          {row['historically_clean_candidates']}")
    print(f"Historically material:       {row['historically_material_candidates']}")
    print(f"Without historical overlap:  {row['without_historical_direct_overlap']}")
    print(f"Candidate comparison rows:   {row['candidate_comparison_rows']}")
    print(f"Candidate material rows:     {row['candidate_material_rows']}")
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
