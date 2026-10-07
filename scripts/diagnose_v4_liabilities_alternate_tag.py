from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


TARGET_BUCKET = "alternate_or_custom_liability_tags"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the single V4 alternate/custom liability-tag residual using "
            "the existing V3 raw SEC evidence. Research-only; no model mutation."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, low_memory=False)
    if "ticker" in frame.columns:
        frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    return frame


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    classification_path = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_residual_classification"
        / "liabilities_residual_detail.csv"
    )
    raw_path = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "liabilities_alternate_tags"
        / "alternate_tag_detail.csv"
    )

    if not classification_path.exists():
        raise SystemExit(f"Missing V4 classification detail: {classification_path}")
    if not raw_path.exists():
        raise SystemExit(f"Missing V3 alternate-tag evidence: {raw_path}")

    classified = _load(classification_path)
    target = classified.loc[
        classified["v4_liabilities_bucket"].astype(str).eq(TARGET_BUCKET)
    ].copy()
    if target.empty:
        raise SystemExit("No V4 alternate/custom liability-tag residual found")

    raw = _load(raw_path)
    tickers = set(target["ticker"])
    raw = raw.loc[raw["ticker"].isin(tickers)].copy()

    rows: list[dict[str, object]] = []
    for ticker in sorted(tickers):
        class_row = target.loc[target["ticker"].eq(ticker)].iloc[0]
        group = raw.loc[raw["ticker"].eq(ticker)].copy()
        if group.empty:
            rows.append(
                {
                    "ticker": ticker,
                    "cik": class_row.get("cik"),
                    "raw_rows": 0,
                    "raw_statuses": "",
                    "context_instants": "",
                    "other_liability_tags": "",
                    "total_like_tags": "",
                    "direct_value": None,
                    "current_value": None,
                    "noncurrent_value": None,
                    "current_plus_noncurrent": None,
                    "reason": "",
                    "diagnostic_status": "missing_raw_alternate_tag_evidence",
                }
            )
            continue

        def vals(column: str) -> str:
            if column not in group.columns:
                return ""
            values = (
                group[column]
                .dropna()
                .astype(str)
                .map(str.strip)
            )
            values = [
                value for value in values
                if value and value.lower() != "nan"
            ]
            return "|".join(dict.fromkeys(values))

        rows.append(
            {
                "ticker": ticker,
                "cik": class_row.get("cik"),
                "raw_rows": int(len(group)),
                "raw_statuses": vals("status"),
                "context_instants": vals("context_instant"),
                "other_liability_tags": vals("other_liability_tags"),
                "total_like_tags": vals("total_like_tags"),
                "direct_value": vals("direct_value"),
                "current_value": vals("current_value"),
                "noncurrent_value": vals("noncurrent_value"),
                "current_plus_noncurrent": vals("current_plus_noncurrent"),
                "reason": vals("reason"),
                "diagnostic_status": (
                    "custom_tag_semantic_review_required"
                    if vals("other_liability_tags")
                    else "alternate_status_without_named_custom_tag"
                ),
            }
        )

    detail = pd.DataFrame(rows)

    output_dir = (
        root
        / "reports"
        / "v4"
        / "data_sources"
        / as_of
        / "liabilities_alternate_tag_diagnostic"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "alternate_tag_diagnostic.csv"
    summary_path = output_dir / "summary.json"
    detail.to_csv(detail_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_LIABILITIES_ALTERNATE_TAG_DIAGNOSTIC_COMPLETE",
        "as_of": as_of,
        "residual_count": int(len(detail)),
        "tickers": sorted(detail["ticker"].tolist()),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 LIABILITIES ALTERNATE/CUSTOM-TAG DIAGNOSTIC")
    print(f"As of:                       {as_of}")
    print(f"Residual tickers:            {len(detail)}")
    print()
    for row in detail.itertuples(index=False):
        print(f"{row.ticker:6s} status={row.raw_statuses or '-'}")
        print(f"       context={row.context_instants or '-'}")
        print(f"       custom_tags={row.other_liability_tags or '-'}")
        print(f"       total_like_tags={row.total_like_tags or '-'}")
        print(f"       reason={row.reason or '-'}")
    print()
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
