from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose material historical identity mismatches for the exact-match "
            "V4 liabilities cohort. Validation-only; no recovery is applied."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    source = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_exact_collapsed_history_review"
        / "collapsed_historical_detail.csv"
    )
    if not source.exists():
        raise SystemExit(f"Missing collapsed V4 history detail: {source}")

    frame = pd.read_csv(source, low_memory=False)
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["ddate_date"] = pd.to_datetime(
        frame["ddate_date"], errors="coerce"
    )
    frame["year"] = frame["ddate_date"].dt.year
    frame["absolute_relative_error"] = pd.to_numeric(
        frame["absolute_relative_error"], errors="coerce"
    )

    material = frame.loc[
        frame["collapsed_validation_band"].astype(str).eq("material_difference")
    ].copy()

    if material.empty:
        raise SystemExit("No material historical mismatches found")

    by_ticker = (
        material.groupby("ticker", as_index=False)
        .agg(
            material_rows=("ticker", "size"),
            first_year=("year", "min"),
            last_year=("year", "max"),
            median_relative_error=("absolute_relative_error", "median"),
            max_relative_error=("absolute_relative_error", "max"),
        )
        .sort_values(["material_rows", "ticker"], ascending=[False, True], kind="stable")
        .reset_index(drop=True)
    )

    by_year = (
        material.groupby(["ticker", "year"], as_index=False)
        .agg(
            material_rows=("ticker", "size"),
            median_relative_error=("absolute_relative_error", "median"),
            max_relative_error=("absolute_relative_error", "max"),
        )
        .sort_values(["ticker", "year"], kind="stable")
        .reset_index(drop=True)
    )

    dimensions = [
        column
        for column in (
            "ticker",
            "form",
            "equity_tag",
            "identity_type",
            "equity_preference",
            "identity_preference",
        )
        if column in material.columns
    ]
    if dimensions:
        pattern = (
            material.groupby(dimensions, dropna=False, as_index=False)
            .agg(
                material_rows=("ticker", "size"),
                median_relative_error=("absolute_relative_error", "median"),
                max_relative_error=("absolute_relative_error", "max"),
            )
            .sort_values(
                ["material_rows", "ticker"],
                ascending=[False, True],
                kind="stable",
            )
            .reset_index(drop=True)
        )
    else:
        pattern = pd.DataFrame()

    clean = frame.loc[
        frame["collapsed_validation_band"].astype(str).isin(
            {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
        )
    ].copy()

    year_mix = (
        frame.groupby(["ticker", "year"], as_index=False)
        .agg(
            filing_rows=("ticker", "size"),
            material_rows=(
                "collapsed_validation_band",
                lambda s: int(pd.Series(s).astype(str).eq("material_difference").sum()),
            ),
        )
        .sort_values(["ticker", "year"], kind="stable")
        .reset_index(drop=True)
    )
    year_mix["material_rate"] = (
        year_mix["material_rows"] / year_mix["filing_rows"]
    )

    issuer_rows = []
    for ticker, group in frame.groupby("ticker"):
        mat = group["collapsed_validation_band"].astype(str).eq("material_difference")
        exactish = group["collapsed_validation_band"].astype(str).isin(
            {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
        )
        issuer_rows.append(
            {
                "ticker": ticker,
                "filing_rows": int(len(group)),
                "clean_rows": int(exactish.sum()),
                "material_rows": int(mat.sum()),
                "material_rate": float(mat.mean()),
                "material_years": "|".join(
                    str(int(y))
                    for y in sorted(
                        set(
                            pd.to_numeric(
                                group.loc[mat, "year"], errors="coerce"
                            ).dropna()
                        )
                    )
                ),
                "max_relative_error": (
                    float(group.loc[mat, "absolute_relative_error"].max())
                    if mat.any()
                    else None
                ),
            }
        )
    issuer_summary = pd.DataFrame(issuer_rows).sort_values("ticker").reset_index(drop=True)

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_exact_material_diagnostic"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    material_path = output_dir / "material_filing_detail.csv"
    issuer_path = output_dir / "issuer_summary.csv"
    year_path = output_dir / "material_by_year.csv"
    pattern_path = output_dir / "material_pattern_summary.csv"
    year_mix_path = output_dir / "issuer_year_material_rates.csv"
    summary_path = output_dir / "summary.json"

    material.to_csv(material_path, index=False)
    issuer_summary.to_csv(issuer_path, index=False)
    by_year.to_csv(year_path, index=False)
    pattern.to_csv(pattern_path, index=False)
    year_mix.to_csv(year_mix_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_EXACT_COHORT_MATERIAL_DIAGNOSTIC_COMPLETE",
        "as_of": as_of,
        "candidate_tickers": sorted(frame["ticker"].unique().tolist()),
        "filing_rows": int(len(frame)),
        "clean_rows": int(len(clean)),
        "material_rows": int(len(material)),
        "issuers_with_material": int(material["ticker"].nunique()),
        "material_year_count": int(material["year"].nunique()),
        "max_relative_error": float(material["absolute_relative_error"].max()),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 EXACT-COHORT MATERIAL HISTORY DIAGNOSTIC")
    print(f"As of:                       {as_of}")
    print(f"Filing rows:                 {len(frame)}")
    print(f"Clean rows:                  {len(clean)}")
    print(f"Material rows:               {len(material)}")
    print(f"Issuers with material:       {material['ticker'].nunique()}")
    print(f"Material years:              {material['year'].nunique()}")
    print(f"Max relative error:          {payload['max_relative_error']}")
    print()
    for row in issuer_summary.itertuples(index=False):
        print(
            f"{row.ticker:6s} material={row.material_rows}/{row.filing_rows} "
            f"rate={row.material_rate:.3f} years={row.material_years} "
            f"max_err={row.max_relative_error}"
        )
    print()
    print(f"Material detail:             {material_path}")
    print(f"Issuer summary:              {issuer_path}")
    print(f"By year:                     {year_path}")
    print(f"Pattern summary:             {pattern_path}")
    print(f"Issuer-year rates:           {year_mix_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
