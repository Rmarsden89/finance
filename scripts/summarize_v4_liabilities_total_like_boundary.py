from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


TARGET_BUCKET = "liabilities_plus_equity_total_only"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize the V4 liabilities-plus-equity-only residual boundary. "
            "This command is research-only and never derives liabilities from "
            "total-like values."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    path = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "liabilities_residual_classification"
        / "liabilities_residual_detail.csv"
    )
    if not path.exists():
        raise SystemExit(f"Missing V4 liabilities classification: {path}")

    detail = pd.read_csv(path, low_memory=False)
    detail["ticker"] = detail["ticker"].astype(str).str.upper().str.strip()
    target = detail.loc[
        detail["v4_liabilities_bucket"].astype(str).eq(TARGET_BUCKET)
    ].copy()
    if target.empty:
        raise SystemExit("No liabilities-plus-equity-only residuals found")

    total_like_columns = [
        column for column in target.columns
        if column.endswith("total_like_tags")
    ]
    other_tag_columns = [
        column for column in target.columns
        if column.endswith("other_liability_tags")
    ]

    def collapse(row: pd.Series, columns: list[str]) -> str:
        vals: list[str] = []
        for column in columns:
            value = str(row.get(column, "") or "").strip()
            if value and value.lower() != "nan":
                vals.extend(part for part in value.split("|") if part)
        return "|".join(dict.fromkeys(vals))

    target["observed_total_like_tags"] = target.apply(
        lambda row: collapse(row, total_like_columns), axis=1
    )
    target["observed_other_liability_tags"] = target.apply(
        lambda row: collapse(row, other_tag_columns), axis=1
    )
    target["semantic_decision"] = (
        "reject_as_total_liabilities_without_separate_validated_equity_rule"
    )

    counts = (
        target["observed_total_like_tags"]
        .replace("", "(none recorded)")
        .value_counts()
        .rename_axis("observed_total_like_tags")
        .reset_index(name="rows")
    )

    output_dir = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "liabilities_total_like_boundary"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "total_like_residual_detail.csv"
    tags_path = output_dir / "total_like_tag_summary.csv"
    summary_path = output_dir / "summary.json"

    target.to_csv(detail_path, index=False)
    counts.to_csv(tags_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_LIABILITIES_TOTAL_LIKE_BOUNDARY_COMPLETE",
        "as_of": as_of,
        "residual_rows": int(len(target)),
        "residual_tickers": int(target["ticker"].nunique()),
        "decision": (
            "reject total-like balance-sheet totals as direct liabilities; "
            "any total-minus-equity recovery requires a separately versioned "
            "and historically validated semantic rule"
        ),
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
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 LIABILITIES TOTAL-LIKE SEMANTIC BOUNDARY")
    print(f"As of:                       {as_of}")
    print(f"Residual rows:               {len(target)}")
    print(f"Residual tickers:            {target['ticker'].nunique()}")
    print()
    print("Observed total-like tags:")
    for row in counts.itertuples(index=False):
        print(f"{int(row.rows):4d}  {row.observed_total_like_tags}")
    print()
    print(
        "Decision:                    REJECT_AS_DIRECT_LIABILITIES"
    )
    print(
        "Reason:                      liabilities-plus-equity totals are not "
        "semantically equal to total liabilities"
    )
    print(
        "Future exception:            only via a separate versioned "
        "total-minus-equity rule with independent historical validation"
    )
    print(f"Detail:                      {detail_path}")
    print(f"Tag summary:                 {tags_path}")
    print(f"Summary:                     {summary_path}")
    print("NO LIABILITIES WERE DERIVED OR WRITTEN BY THIS COMMAND.")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
