from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.raw_share_validation import (
    build_raw_share_candidate,
    raw_share_validation_row,
)
from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the frozen raw-SEC shares rule to the Issue #26 validation "
            "cohort and compare supported controls against canonical V2 shares."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--raw-sec-subdir",
        default="raw_sec_share_validation",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()
    cohort_path = base / "raw_share_validation" / "validation_cohort.csv"
    facts_path = base / args.raw_sec_subdir / "inline_xbrl_candidate_facts.csv"

    required_paths = {
        "validation cohort": cohort_path,
        "raw SEC candidate facts": facts_path,
    }
    missing_paths = [
        f"{name}: {path}"
        for name, path in required_paths.items()
        if not path.exists()
    ]
    if missing_paths:
        raise SystemExit(
            "Missing raw-share validation input(s):\n  "
            + "\n  ".join(missing_paths)
        )

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    snapshot_path = v2["current_snapshot"]
    if not snapshot_path.exists():
        raise SystemExit(f"Missing V2 current snapshot: {snapshot_path}")

    cohort = pd.read_csv(cohort_path, low_memory=False)
    facts = pd.read_csv(facts_path, low_memory=False)
    snapshot = pd.read_csv(snapshot_path, low_memory=False)

    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    facts["ticker"] = facts["ticker"].astype(str).str.upper().str.strip()
    snapshot["ticker"] = snapshot["ticker"].astype(str).str.upper().str.strip()

    provenance_columns = [
        column
        for column in (
            "ticker",
            "shares_outstanding",
            "shares_outstanding_period_date",
            "shares_outstanding_filing_period_date",
            "shares_outstanding_filed_date",
            "shares_outstanding_accepted_at",
            "shares_outstanding_form",
            "shares_outstanding_source_tag",
        )
        if column in snapshot.columns
    ]
    snapshot_provenance = (
        snapshot[provenance_columns]
        .drop_duplicates("ticker", keep="first")
        .set_index("ticker")
        .to_dict(orient="index")
    )

    rows: list[dict[str, object]] = []
    for cohort_row in cohort.itertuples(index=False):
        ticker = str(cohort_row.ticker).upper()
        candidate = build_raw_share_candidate(
            facts.loc[facts["ticker"].eq(ticker)].copy(),
            ticker=ticker,
            as_of=args.as_of,
        )
        canonical = snapshot_provenance.get(ticker, {})
        validation = raw_share_validation_row(
            candidate=candidate,
            canonical_value=canonical.get("shares_outstanding"),
        )
        for column in (
            "shares_outstanding_period_date",
            "shares_outstanding_filing_period_date",
            "shares_outstanding_filed_date",
            "shares_outstanding_accepted_at",
            "shares_outstanding_form",
            "shares_outstanding_source_tag",
        ):
            validation[f"canonical_{column}"] = canonical.get(column, "")
        validation.update(
            {
                "sample_cohort": getattr(cohort_row, "sample_cohort", ""),
                "raw_share_validation_role": getattr(
                    cohort_row, "raw_share_validation_role", ""
                ),
                "cik": getattr(cohort_row, "cik", ""),
                "company_name": getattr(cohort_row, "company_name", ""),
            }
        )
        rows.append(validation)

    detail = pd.DataFrame(rows)

    raw_instant = pd.to_datetime(detail["context_instant"], errors="coerce")
    canonical_period = pd.to_datetime(
        detail["canonical_shares_outstanding_period_date"], errors="coerce"
    )
    detail["raw_vs_canonical_period_days"] = (
        raw_instant - canonical_period
    ).dt.days
    detail["same_period_date"] = (
        raw_instant.notna()
        & canonical_period.notna()
        & raw_instant.eq(canonical_period)
    )
    detail["diagnostic_classification"] = "not_comparable"
    comparable_mask = detail["validation_band"].astype(str).ne("not_comparable")
    detail.loc[
        comparable_mask & detail["same_period_date"],
        "diagnostic_classification",
    ] = "same_period"
    detail.loc[
        comparable_mask
        & ~detail["same_period_date"]
        & detail["raw_vs_canonical_period_days"].notna(),
        "diagnostic_classification",
    ] = "different_period"
    detail.loc[
        comparable_mask
        & detail["raw_vs_canonical_period_days"].isna(),
        "diagnostic_classification",
    ] = "canonical_period_missing"

    detail = detail[
        [
            "ticker",
            "cik",
            "company_name",
            "sample_cohort",
            "raw_share_validation_role",
            "status",
            "value",
            "accession",
            "accepted_at",
            "canonical_value",
            "canonical_shares_outstanding_period_date",
            "canonical_shares_outstanding_filing_period_date",
            "canonical_shares_outstanding_filed_date",
            "canonical_shares_outstanding_accepted_at",
            "canonical_shares_outstanding_form",
            "canonical_shares_outstanding_source_tag",
            "absolute_difference",
            "absolute_relative_error",
            "validation_band",
            "diagnostic_classification",
            "same_period_date",
            "raw_vs_canonical_period_days",
            "context_instant",
            "selection_rule",
            "component_count",
            "component_dimensions",
            "component_values",
            "reason",
        ]
    ].sort_values(
        ["raw_share_validation_role", "ticker"],
        kind="stable",
    ).reset_index(drop=True)

    controls = detail.loc[
        detail["raw_share_validation_role"].astype(str).eq("control")
    ]
    residuals = detail.loc[
        detail["raw_share_validation_role"].astype(str).eq("residual")
    ]

    candidate_status = (
        detail.groupby(
            ["raw_share_validation_role", "status"],
            dropna=False,
            as_index=False,
        )
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(
            ["raw_share_validation_role", "status"],
            kind="stable",
        )
    )
    period_diagnostics = (
        controls.groupby(
            ["validation_band", "diagnostic_classification"],
            dropna=False,
            as_index=False,
        )
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(
            ["validation_band", "diagnostic_classification"],
            kind="stable",
        )
    )

    validation_bands = (
        controls.groupby("validation_band", dropna=False, as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values("validation_band", kind="stable")
    )

    comparable_controls = controls.loc[
        controls["validation_band"].astype(str).ne("not_comparable")
    ]
    within_1 = comparable_controls["validation_band"].isin(
        {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
    )

    summary = {
        "as_of": args.as_of.isoformat(),
        "cohort_rows": int(len(detail)),
        "control_rows": int(len(controls)),
        "residual_rows": int(len(residuals)),
        "candidate_rows": int(detail["status"].eq("candidate").sum()),
        "residual_candidates_recovered": int(
            residuals["status"].eq("candidate").sum()
        ),
        "comparable_controls": int(len(comparable_controls)),
        "controls_within_1_pct": int(within_1.sum()),
        "controls_within_1_pct_rate": (
            float(within_1.mean()) if len(within_1) else None
        ),
        "control_exact_matches": int(
            comparable_controls["validation_band"].eq("exact_match").sum()
        ),
        "control_material_differences": int(
            comparable_controls["validation_band"]
            .eq("material_difference")
            .sum()
        ),
        "material_differences_same_period": int(
            (
                comparable_controls["validation_band"].eq("material_difference")
                & comparable_controls["same_period_date"].fillna(False)
            ).sum()
        ),
        "material_differences_different_period": int(
            (
                comparable_controls["validation_band"].eq("material_difference")
                & comparable_controls["diagnostic_classification"].eq(
                    "different_period"
                )
            ).sum()
        ),
        "research_only": True,
        "model_inputs_modified": False,
    }

    output_dir = base / "raw_share_validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "candidate_validation_detail.csv"
    status_path = output_dir / "candidate_status_summary.csv"
    band_path = output_dir / "control_validation_bands.csv"
    period_path = output_dir / "control_period_diagnostics.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    candidate_status.to_csv(status_path, index=False)
    validation_bands.to_csv(band_path, index=False)
    period_diagnostics.to_csv(period_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 RAW SEC SHARE VALIDATION")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Cohort rows:                 {summary['cohort_rows']}")
    print(f"Controls:                    {summary['control_rows']}")
    print(f"Residual share cases:        {summary['residual_rows']}")
    print(f"Residual candidates:         {summary['residual_candidates_recovered']}")
    print(f"Comparable controls:         {summary['comparable_controls']}")
    print(f"Controls within 1%:          {summary['controls_within_1_pct']}")
    print(f"Exact control matches:       {summary['control_exact_matches']}")
    print(f"Material differences:        {summary['control_material_differences']}")
    print(
        f"  same canonical period:     "
        f"{summary['material_differences_same_period']}"
    )
    print(
        f"  different period:          "
        f"{summary['material_differences_different_period']}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"Status summary:              {status_path}")
    print(f"Validation bands:            {band_path}")
    print(f"Period diagnostics:          {period_path}")
    print(f"Summary:                     {summary_path}")
    print("NO RAW SEC CANDIDATES WERE PROMOTED INTO V1 OR V2.")


if __name__ == "__main__":
    main()
