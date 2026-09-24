from __future__ import annotations

import argparse
from dataclasses import asdict
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
            "Audit the Issue #26 liabilities alternate-tag cohort using raw SEC "
            "filing evidence only."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--raw-sec-subdir",
        default="raw_sec_liabilities_alternate_tags",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = root / "reports" / "v3" / "data_sources" / args.as_of.isoformat()

    cohort_path = base / "liabilities_alternate_tags" / "validation_cohort.csv"
    facts_path = base / args.raw_sec_subdir / "inline_xbrl_candidate_facts.csv"
    manifest_path = base / args.raw_sec_subdir / "filing_manifest.csv"

    required = {
        "alternate-tag cohort": cohort_path,
        "raw SEC candidate facts": facts_path,
        "filing manifest": manifest_path,
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    if missing:
        raise SystemExit(
            "Missing liabilities alternate-tag input(s):\n  "
            + "\n  ".join(missing)
        )

    cohort = pd.read_csv(cohort_path, low_memory=False)
    facts = pd.read_csv(facts_path, low_memory=False)
    manifest = pd.read_csv(manifest_path, low_memory=False)

    cohort["ticker"] = cohort["ticker"].astype(str).str.upper().str.strip()
    facts["ticker"] = facts["ticker"].astype(str).str.upper().str.strip()
    manifest["ticker"] = manifest["ticker"].astype(str).str.upper().str.strip()

    manifest_lookup = (
        manifest.drop_duplicates("ticker", keep="first")
        .set_index("ticker")
        .to_dict(orient="index")
    )

    rows: list[dict[str, object]] = []
    for row in cohort.itertuples(index=False):
        ticker = str(row.ticker).upper()
        evidence = classify_alternate_liabilities_evidence(
            facts.loc[facts["ticker"].eq(ticker)].copy(),
            ticker=ticker,
            as_of=args.as_of,
        )
        output = {
            **asdict(evidence),
            "cik": getattr(row, "cik", ""),
            "company_name": getattr(row, "company_name", ""),
            "original_classification": getattr(row, "classification", ""),
            "inventory_liability_tags": getattr(
                row, "current_filing_liability_tags", ""
            ),
            "inventory_total_like_tags": getattr(
                row, "current_filing_total_like_tags", ""
            ),
        }
        for key, value in manifest_lookup.get(ticker, {}).items():
            if key == "ticker":
                continue
            output[f"manifest_{key}"] = value
        rows.append(output)

    detail = pd.DataFrame(rows).sort_values(
        ["status", "ticker"], kind="stable"
    ).reset_index(drop=True)

    status_summary = (
        detail.groupby("status", dropna=False, as_index=False)
        .agg(rows=("ticker", "size"), tickers=("ticker", "nunique"))
        .sort_values(["rows", "status"], ascending=[False, True], kind="stable")
    )

    summary = {
        "as_of": args.as_of.isoformat(),
        "cohort_rows": int(len(detail)),
        "raw_direct_liabilities_candidates": int(
            detail["status"].eq("raw_direct_liabilities_candidate").sum()
        ),
        "current_plus_noncurrent_candidates": int(
            detail["status"].eq("current_plus_noncurrent_candidate").sum()
        ),
        "total_like_only": int(detail["status"].eq("total_like_only").sum()),
        "alternate_tags_only": int(
            detail["status"].eq("alternate_tags_only").sum()
        ),
        "no_eligible_evidence": int(
            detail["status"]
            .eq("no_eligible_undimensioned_liability_evidence")
            .sum()
        ),
        "research_only": True,
        "model_inputs_modified": False,
        "paid_vendor_used": False,
    }

    output_dir = base / "liabilities_alternate_tags"
    detail_path = output_dir / "alternate_tag_detail.csv"
    status_path = output_dir / "status_summary.csv"
    summary_path = output_dir / "summary.json"

    detail.to_csv(detail_path, index=False)
    status_summary.to_csv(status_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 SEC-ONLY LIABILITIES ALTERNATE-TAG AUDIT")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Cohort rows:                 {summary['cohort_rows']}")
    print(
        f"Raw direct candidates:       "
        f"{summary['raw_direct_liabilities_candidates']}"
    )
    print(
        f"Current + noncurrent:        "
        f"{summary['current_plus_noncurrent_candidates']}"
    )
    print(f"Total-like only:             {summary['total_like_only']}")
    print(f"Alternate tags only:         {summary['alternate_tags_only']}")
    print(f"No eligible evidence:        {summary['no_eligible_evidence']}")
    print(f"Detail:                      {detail_path}")
    print(f"Status summary:              {status_path}")
    print(f"Summary:                     {summary_path}")
    print("NO PAID VENDOR WAS USED.")
    print("NO LIABILITIES VALUES WERE PROMOTED INTO V1 OR V2.")


if __name__ == "__main__":
    main()
