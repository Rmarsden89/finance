from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.v2 import resolve_v2_liabilities_audit_paths


TARGET = "AES"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate AES as a narrow V4 same-context Assets-Equity liabilities "
            "candidate using current V2 audit evidence and existing collapsed "
            "historical identity corroboration. Validation-only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    audit_path = resolve_v2_liabilities_audit_paths(
        root, args.as_of
    )["detail"]
    if not audit_path.exists():
        raise SystemExit(f"Missing V2 liabilities audit: {audit_path}")

    audit = pd.read_csv(audit_path, low_memory=False)
    audit["ticker"] = audit["ticker"].astype(str).str.upper().str.strip()
    current = audit.loc[audit["ticker"].eq(TARGET)].copy()
    if current.empty:
        raise SystemExit("AES missing from V2 liabilities audit")
    current_row = current.iloc[0]

    collapsed_path = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_historical_validation"
        / "current_candidate_identity_collapsed.csv"
    )
    if not collapsed_path.exists():
        raise SystemExit(
            f"Missing collapsed historical identity evidence: {collapsed_path}"
        )

    collapsed = pd.read_csv(collapsed_path, low_memory=False)
    collapsed["ticker"] = collapsed["ticker"].astype(str).str.upper().str.strip()
    aes = collapsed.loc[collapsed["ticker"].eq(TARGET)].copy()
    if aes.empty:
        raise SystemExit("No collapsed historical identity evidence found for AES")

    aes["ddate_date"] = pd.to_datetime(
        aes["ddate_date"], errors="coerce"
    ).dt.normalize()
    aes["absolute_relative_error"] = pd.to_numeric(
        aes["absolute_relative_error"], errors="coerce"
    )

    bands = aes["collapsed_validation_band"].astype(str)
    clean = bands.isin(
        {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
    )
    material = bands.eq("material_difference")
    ambiguous = bands.eq("ambiguous")

    by_year = (
        aes.assign(year=aes["ddate_date"].dt.year)
        .groupby("year", as_index=False)
        .agg(
            filing_rows=("ticker", "size"),
            clean_rows=(
                "collapsed_validation_band",
                lambda s: int(pd.Series(s).astype(str).isin(
                    {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
                ).sum()),
            ),
            material_rows=(
                "collapsed_validation_band",
                lambda s: int(pd.Series(s).astype(str).eq("material_difference").sum()),
            ),
            ambiguous_rows=(
                "collapsed_validation_band",
                lambda s: int(pd.Series(s).astype(str).eq("ambiguous").sum()),
            ),
            max_relative_error=("absolute_relative_error", "max"),
        )
        .sort_values("year", kind="stable")
        .reset_index(drop=True)
    )

    material_years = sorted(
        set(
            pd.to_numeric(
                aes.loc[material, "ddate_date"].dt.year, errors="coerce"
            ).dropna().astype(int)
        )
    )

    last_material_period = (
        aes.loc[material, "ddate_date"].max()
        if material.any()
        else pd.NaT
    )
    post = (
        aes.loc[aes["ddate_date"].gt(last_material_period)].copy()
        if pd.notna(last_material_period)
        else aes.copy()
    )
    post_bands = post["collapsed_validation_band"].astype(str)
    post_clean = bool(
        len(post) > 0
        and not post_bands.eq("material_difference").any()
        and not post_bands.eq("ambiguous").any()
    )

    payload = {
        "schema_version": 1,
        "status": "V4_AES_IDENTITY_HISTORY_REVIEW_COMPLETE",
        "as_of": as_of,
        "ticker": TARGET,
        "cik": (
            int(float(current_row.get("cik")))
            if pd.notna(current_row.get("cik"))
            else None
        ),
        "current_v2_classification": str(
            current_row.get("classification", "")
        ),
        "current_recommended_action": str(
            current_row.get("recommended_action", "")
        ),
        "current_identity_candidate_count": int(
            pd.to_numeric(
                pd.Series(
                    [current_row.get("same_context_identity_candidate_count")]
                ),
                errors="coerce",
            ).fillna(0).iloc[0]
        ),
        "current_identity_values": str(
            current_row.get("identity_derived_liabilities_values", "")
        ),
        "historical_filing_rows": int(len(aes)),
        "historically_clean_rows": int(clean.sum()),
        "material_rows": int(material.sum()),
        "ambiguous_rows": int(ambiguous.sum()),
        "material_years": material_years,
        "max_relative_error": (
            float(aes.loc[material, "absolute_relative_error"].max())
            if material.any()
            else None
        ),
        "last_material_period": (
            last_material_period.date().isoformat()
            if pd.notna(last_material_period)
            else None
        ),
        "post_material_rows": int(len(post)),
        "post_material_clean": post_clean,
        "first_post_material_period": (
            post["ddate_date"].min().date().isoformat()
            if len(post)
            else None
        ),
        "research_only": True,
        "assets_minus_equity_used_for_recovery": False,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "aes_identity_history_review"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "historical_identity_detail.csv"
    year_path = output_dir / "historical_identity_by_year.csv"
    summary_path = output_dir / "summary.json"

    aes.to_csv(detail_path, index=False)
    by_year.to_csv(year_path, index=False)
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 AES SAME-CONTEXT IDENTITY HISTORY REVIEW")
    print(f"As of:                       {as_of}")
    print(
        f"Current identity candidates: "
        f"{payload['current_identity_candidate_count']}"
    )
    print(
        f"Current derived values:      "
        f"{payload['current_identity_values'] or '-'}"
    )
    print(f"Historical filing rows:      {payload['historical_filing_rows']}")
    print(f"Historically clean rows:     {payload['historically_clean_rows']}")
    print(f"Material rows:               {payload['material_rows']}")
    print(f"Ambiguous rows:              {payload['ambiguous_rows']}")
    print(
        "Material years:              "
        + (
            "|".join(str(year) for year in material_years)
            if material_years
            else "-"
        )
    )
    print(
        f"Max relative error:          "
        f"{payload['max_relative_error']}"
    )
    print(
        f"Last material period:        "
        f"{payload['last_material_period']}"
    )
    print(
        f"Post-material rows:          "
        f"{payload['post_material_rows']}"
    )
    print(
        f"Post-material clean:         "
        f"{payload['post_material_clean']}"
    )
    print(
        f"First post-material period:  "
        f"{payload['first_post_material_period']}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"By year:                     {year_path}")
    print(f"Summary:                     {summary_path}")
    print("ASSETS - EQUITY REMAINS VALIDATION-ONLY IN THIS COMMAND.")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
