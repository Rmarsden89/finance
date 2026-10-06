from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.liabilities_audit import (
    build_same_context_liabilities_identities,
)
from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the Issue #30 V4 current+noncurrent liabilities cohort "
            "against same-context Assets - Equity evidence. The identity is "
            "validation-only and is never used as a recovery value."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _band(relative_error: float | None, absolute_difference: float | None) -> str:
    if relative_error is None or absolute_difference is None:
        return "not_comparable"
    if absolute_difference == 0:
        return "exact_match"
    if relative_error <= 0.0001:
        return "within_0_01_pct"
    if relative_error <= 0.001:
        return "within_0_1_pct"
    if relative_error <= 0.01:
        return "within_1_pct"
    return "material_difference"


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    base = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_residual_classification"
    )
    cohort_path = base / "priority_v4_liabilities_candidates.csv"
    if not cohort_path.exists():
        raise SystemExit(f"Missing V4 liabilities priority cohort: {cohort_path}")

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    candidates_path = v2["sec_candidates"]
    if not candidates_path.exists():
        raise SystemExit(f"Missing saved V2 SEC candidates: {candidates_path}")

    cohort = pd.read_csv(cohort_path, low_memory=False)
    candidates = pd.read_csv(candidates_path, low_memory=False)

    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    candidates["ticker"] = candidates["ticker"].astype(str).str.upper().str.strip()

    identities = build_same_context_liabilities_identities(
        candidates,
        as_of=args.as_of,
    )
    if not identities.empty:
        identities["ticker"] = identities["ticker"].astype(str).str.upper().str.strip()
        identities["ddate_date"] = pd.to_datetime(
            identities["ddate_date"], errors="coerce"
        ).dt.date.astype("string")

    rows: list[dict[str, object]] = []
    for row in cohort.itertuples(index=False):
        ticker = str(row.ticker).upper()
        constructed = pd.to_numeric(
            pd.Series(
                [
                    getattr(row, "identity_raw_current_plus_noncurrent", pd.NA)
                    if pd.notna(
                        getattr(row, "identity_raw_current_plus_noncurrent", pd.NA)
                    )
                    else getattr(
                        row,
                        "alternate_raw_current_plus_noncurrent",
                        pd.NA,
                    )
                ]
            ),
            errors="coerce",
        ).iloc[0]
        context = str(
            getattr(row, "identity_raw_context_instant", "") or ""
        ).strip()
        if not context or context.lower() == "nan":
            context = str(
                getattr(row, "alternate_raw_context_instant", "") or ""
            ).strip()

        matches = identities.loc[identities["ticker"].eq(ticker)].copy()
        if context and context.lower() != "nan" and "ddate_date" in matches.columns:
            matches = matches.loc[matches["ddate_date"].astype(str).eq(context)]

        derived_values = sorted(
            set(
                pd.to_numeric(
                    matches.get(
                        "derived_liabilities",
                        pd.Series(dtype="float64"),
                    ),
                    errors="coerce",
                ).dropna()
            )
        )

        identity_value = (
            float(derived_values[0]) if len(derived_values) == 1 else None
        )
        constructed_value = (
            float(constructed) if pd.notna(constructed) else None
        )
        if (
            constructed_value is not None
            and identity_value is not None
            and identity_value > 0
        ):
            absolute_difference = abs(constructed_value - identity_value)
            relative_error = absolute_difference / identity_value
        else:
            absolute_difference = None
            relative_error = None

        rows.append(
            {
                "ticker": ticker,
                "cik": getattr(row, "cik", ""),
                "company_name": getattr(row, "company_name", ""),
                "context_instant": context,
                "current_liabilities": (
                    getattr(row, "identity_raw_current_value", pd.NA)
                    if pd.notna(getattr(row, "identity_raw_current_value", pd.NA))
                    else getattr(row, "alternate_raw_current_value", pd.NA)
                ),
                "noncurrent_liabilities": (
                    getattr(row, "identity_raw_noncurrent_value", pd.NA)
                    if pd.notna(
                        getattr(row, "identity_raw_noncurrent_value", pd.NA)
                    )
                    else getattr(row, "alternate_raw_noncurrent_value", pd.NA)
                ),
                "current_plus_noncurrent": constructed_value,
                "same_context_identity_candidates": int(len(matches)),
                "unique_identity_values": int(len(derived_values)),
                "assets_minus_equity_validation_value": identity_value,
                "absolute_difference": absolute_difference,
                "absolute_relative_error": relative_error,
                "validation_band": _band(
                    relative_error,
                    absolute_difference,
                ),
                "identity_used_for_recovery": False,
            }
        )

    detail = pd.DataFrame(rows).sort_values(
        ["validation_band", "ticker"], kind="stable"
    ).reset_index(drop=True)

    counts = detail["validation_band"].value_counts().to_dict()
    comparable = detail.loc[
        detail["validation_band"].ne("not_comparable")
    ].copy()

    summary = {
        "schema_version": 1,
        "status": "V4_CURRENT_NONCURRENT_IDENTITY_VALIDATION_COMPLETE",
        "as_of": as_of,
        "cohort_rows": int(len(detail)),
        "comparable_rows": int(len(comparable)),
        "exact_matches": int(counts.get("exact_match", 0)),
        "within_0_01_pct": int(counts.get("within_0_01_pct", 0)),
        "within_0_1_pct": int(counts.get("within_0_1_pct", 0)),
        "within_1_pct": int(counts.get("within_1_pct", 0)),
        "material_differences": int(counts.get("material_difference", 0)),
        "not_comparable": int(counts.get("not_comparable", 0)),
        "max_absolute_relative_error": (
            float(comparable["absolute_relative_error"].max())
            if len(comparable)
            else None
        ),
        "assets_minus_equity_used_for_recovery": False,
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_current_noncurrent_validation"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "current_noncurrent_identity_validation.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 CURRENT+NONCURRENT LIABILITIES IDENTITY VALIDATION")
    print(f"As of:                       {as_of}")
    print(f"Cohort rows:                 {summary['cohort_rows']}")
    print(f"Comparable rows:             {summary['comparable_rows']}")
    print(f"Exact matches:               {summary['exact_matches']}")
    print(f"Within 0.01%:                {summary['within_0_01_pct']}")
    print(f"Within 0.1%:                 {summary['within_0_1_pct']}")
    print(f"Within 1%:                   {summary['within_1_pct']}")
    print(f"Material differences:        {summary['material_differences']}")
    print(f"Not comparable:              {summary['not_comparable']}")
    print(
        "Max relative error:          "
        f"{summary['max_absolute_relative_error']}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("ASSETS - EQUITY WAS VALIDATION ONLY; IT WAS NOT USED FOR RECOVERY.")
    print("V1/V2/V3 ARTIFACTS WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
