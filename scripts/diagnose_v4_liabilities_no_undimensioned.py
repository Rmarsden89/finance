from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


TARGET_BUCKET = "no_eligible_undimensioned_raw_liability_evidence"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the final V4 liabilities residual with no eligible "
            "undimensioned raw liability evidence."
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


def _values(frame: pd.DataFrame, column: str) -> str:
    if frame.empty or column not in frame.columns:
        return ""
    vals = (
        frame[column]
        .dropna()
        .astype(str)
        .map(str.strip)
    )
    vals = [v for v in vals if v and v.lower() != "nan"]
    return "|".join(dict.fromkeys(vals))


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    classification_path = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "liabilities_residual_classification"
        / "liabilities_residual_detail.csv"
    )
    if not classification_path.exists():
        raise SystemExit(f"Missing V4 classification detail: {classification_path}")

    classified = _load(classification_path)
    target = classified.loc[
        classified["v4_liabilities_bucket"].astype(str).eq(TARGET_BUCKET)
    ].copy()
    if target.empty:
        raise SystemExit("No residual found for target bucket")

    raw_paths = [
        root / "reports" / "v3" / "data_sources" / as_of
        / "liabilities_identity_raw_strict" / "raw_sec_detail.csv",
        root / "reports" / "v3" / "data_sources" / as_of
        / "liabilities_no_supported_current" / "raw_sec_detail.csv",
        root / "reports" / "v3" / "data_sources" / as_of
        / "liabilities_alternate_tags" / "alternate_tag_detail.csv",
    ]

    raw_frames = []
    for path in raw_paths:
        frame = _load(path)
        if not frame.empty:
            frame["_source_artifact"] = str(path)
            raw_frames.append(frame)
    raw = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()

    rows = []
    for row in target.itertuples(index=False):
        ticker = str(row.ticker).upper()
        group = raw.loc[raw["ticker"].eq(ticker)].copy() if not raw.empty else pd.DataFrame()

        rows.append({
            "ticker": ticker,
            "cik": getattr(row, "cik", None),
            "raw_rows": int(len(group)),
            "raw_statuses": _values(group, "status"),
            "raw_reasons": _values(group, "reason"),
            "context_instants": _values(group, "context_instant"),
            "other_liability_tags": _values(group, "other_liability_tags"),
            "total_like_tags": _values(group, "total_like_tags"),
            "direct_value": _values(group, "direct_value"),
            "current_value": _values(group, "current_value"),
            "noncurrent_value": _values(group, "noncurrent_value"),
            "current_plus_noncurrent": _values(group, "current_plus_noncurrent"),
            "source_artifacts": _values(group, "_source_artifact"),
            "v2_classification": getattr(row, "liabilities_classification", ""),
            "v2_action": getattr(row, "liabilities_recommended_action", ""),
        })

    detail = pd.DataFrame(rows)

    output_dir = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "liabilities_no_undimensioned_diagnostic"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "diagnostic.csv"
    summary_path = output_dir / "summary.json"
    detail.to_csv(detail_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_NO_UNDIMENSIONED_LIABILITIES_DIAGNOSTIC_COMPLETE",
        "as_of": as_of,
        "residual_count": int(len(detail)),
        "tickers": detail["ticker"].tolist(),
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 NO-ELIGIBLE-UNDIMENSIONED LIABILITIES DIAGNOSTIC")
    print(f"As of:                       {as_of}")
    print(f"Residual tickers:            {len(detail)}")
    print()
    for r in detail.itertuples(index=False):
        print(f"{r.ticker:6s} raw_rows={r.raw_rows}")
        print(f"       statuses={r.raw_statuses or '-'}")
        print(f"       reasons={r.raw_reasons or '-'}")
        print(f"       context={r.context_instants or '-'}")
        print(f"       liability_tags={r.other_liability_tags or '-'}")
        print(f"       total_like_tags={r.total_like_tags or '-'}")
        print(f"       direct={r.direct_value or '-'}")
        print(f"       current={r.current_value or '-'}")
        print(f"       noncurrent={r.noncurrent_value or '-'}")
        print(f"       current_plus_noncurrent={r.current_plus_noncurrent or '-'}")
    print()
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
