from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.research.liabilities_alternate_tags import (
    classify_alternate_liabilities_evidence,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate same-filing current + noncurrent liabilities against "
            "direct us-gaap:Liabilities on a deterministic SEC-only control cohort."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--raw-sec-subdir",
        default="raw_sec_liabilities_current_noncurrent_controls",
    )
    parser.add_argument(
        "--cohort-subdir",
        default="liabilities_current_noncurrent",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()

    cohort_path = base / args.cohort_subdir / "validation_cohort.csv"
    facts_path = base / args.raw_sec_subdir / "inline_xbrl_candidate_facts.csv"
    if not cohort_path.exists():
        raise SystemExit(f"Missing control cohort: {cohort_path}")
    if not facts_path.exists():
        raise SystemExit(f"Missing raw SEC facts: {facts_path}")

    cohort = pd.read_csv(cohort_path, low_memory=False)
    facts = pd.read_csv(facts_path, low_memory=False)
    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    facts["ticker"] = facts["ticker"].astype(str).str.upper().str.strip()

    rows: list[dict[str, object]] = []
    for row in cohort.itertuples(index=False):
        ticker = str(row.ticker).upper()
        evidence = classify_alternate_liabilities_evidence(
            facts.loc[facts["ticker"].eq(ticker)].copy(),
            ticker=ticker,
            as_of=args.as_of,
        )
        direct = evidence.direct_value
        constructed = evidence.current_plus_noncurrent
        comparable = (
            direct is not None
            and constructed is not None
            and direct > 0
        )
        if comparable:
            absolute_difference = abs(constructed - direct)
            relative_error = absolute_difference / direct
            if absolute_difference == 0:
                band = "exact_match"
            elif relative_error <= 0.0001:
                band = "within_0_01_pct"
            elif relative_error <= 0.001:
                band = "within_0_1_pct"
            elif relative_error <= 0.01:
                band = "within_1_pct"
            else:
                band = "material_difference"
        else:
            absolute_difference = None
            relative_error = None
            band = "not_comparable"

        rows.append(
            {
                "ticker": ticker,
                "cik": getattr(row, "cik", ""),
                "company_name": getattr(row, "company_name", ""),
                "context_instant": evidence.context_instant,
                "status": evidence.status,
                "direct_liabilities": direct,
                "current_liabilities": evidence.current_value,
                "noncurrent_liabilities": evidence.noncurrent_value,
                "current_plus_noncurrent": constructed,
                "absolute_difference": absolute_difference,
                "absolute_relative_error": relative_error,
                "validation_band": band,
                "reason": evidence.reason,
            }
        )

    detail = pd.DataFrame(rows).sort_values(
        ["validation_band", "ticker"], kind="stable"
    ).reset_index(drop=True)

    comparable = detail.loc[
        detail["validation_band"].astype(str).ne("not_comparable")
    ].copy()
    bands = comparable["validation_band"].value_counts().to_dict()

    summary = {
        "as_of": args.as_of.isoformat(),
        "control_rows": int(len(detail)),
        "comparable_rows": int(len(comparable)),
        "exact_matches": int(bands.get("exact_match", 0)),
        "within_0_01_pct": int(bands.get("within_0_01_pct", 0)),
        "within_0_1_pct": int(bands.get("within_0_1_pct", 0)),
        "within_1_pct": int(bands.get("within_1_pct", 0)),
        "material_differences": int(bands.get("material_difference", 0)),
        "within_0_1_pct_rate": (
            float(
                (
                    bands.get("exact_match", 0)
                    + bands.get("within_0_01_pct", 0)
                    + bands.get("within_0_1_pct", 0)
                )
                / len(comparable)
            )
            if len(comparable)
            else None
        ),
        "max_absolute_relative_error": (
            float(comparable["absolute_relative_error"].max())
            if len(comparable)
            else None
        ),
        "research_only": True,
        "model_inputs_modified": False,
        "paid_vendor_used": False,
    }

    output_dir = base / "liabilities_current_noncurrent"
    detail_path = output_dir / "control_validation_detail.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 SEC-ONLY CURRENT+NONCURRENT LIABILITIES VALIDATION")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Controls:                    {summary['control_rows']}")
    print(f"Comparable rows:             {summary['comparable_rows']}")
    print(f"Exact matches:               {summary['exact_matches']}")
    print(f"Within 0.01%:                {summary['within_0_01_pct']}")
    print(f"Within 0.1%:                 {summary['within_0_1_pct']}")
    print(f"Within 1%:                   {summary['within_1_pct']}")
    print(f"Material differences:        {summary['material_differences']}")
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("NO PAID VENDOR WAS USED.")
    print("NO LIABILITIES VALUES WERE PROMOTED INTO V1 OR V2.")


if __name__ == "__main__":
    main()
