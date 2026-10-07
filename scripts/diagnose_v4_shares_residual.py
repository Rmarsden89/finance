from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose the single post-V3 shares_outstanding residual using the "
            "frozen V4 baseline and existing V3 share-recovery artifacts."
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

    baseline_path = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "post_v3_gap_inventory"
        / "post_v3_residual_gap_inventory.csv"
    )
    if not baseline_path.exists():
        raise SystemExit(f"Missing V4 residual inventory: {baseline_path}")

    residual = _load(baseline_path)
    shares = residual.loc[
        residual["post_v3_shares_gap"].fillna(False).astype(bool)
    ].copy()
    if shares.empty:
        raise SystemExit("No post-V3 shares residual found")

    # Search known V3 report artifacts for the target ticker rather than
    # assuming one specific filename/layout.
    v3_base = root / "reports" / "v3" / "data_sources" / as_of
    frames: list[pd.DataFrame] = []
    for path in sorted(v3_base.rglob("*.csv")):
        try:
            frame = _load(path)
        except Exception:
            continue
        if frame.empty or "ticker" not in frame.columns:
            continue
        match = frame.loc[frame["ticker"].isin(set(shares["ticker"]))].copy()
        if match.empty:
            continue
        match["_source_artifact"] = str(path)
        frames.append(match)

    evidence = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rows = []
    for row in shares.itertuples(index=False):
        ticker = str(row.ticker).upper()
        group = (
            evidence.loc[evidence["ticker"].eq(ticker)].copy()
            if not evidence.empty
            else pd.DataFrame()
        )
        rows.append({
            "ticker": ticker,
            "cik": getattr(row, "cik", None),
            "company_name": getattr(row, "company_name", ""),
            "evidence_rows": int(len(group)),
            "statuses": _values(group, "status"),
            "reasons": _values(group, "reason"),
            "selection_rules": _values(group, "selection_rule"),
            "context_instants": _values(group, "context_instant"),
            "accessions": _values(group, "accession"),
            "accepted_at": _values(group, "accepted_at"),
            "component_counts": _values(group, "component_count"),
            "component_dimensions": _values(group, "component_dimensions"),
            "component_values": _values(group, "component_values"),
            "candidate_values": _values(group, "value"),
            "canonical_values": _values(group, "canonical_value"),
            "validation_bands": _values(group, "validation_band"),
            "source_artifacts": _values(group, "_source_artifact"),
        })

    detail = pd.DataFrame(rows)

    output_dir = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "shares_residual_diagnostic"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "shares_residual_diagnostic.csv"
    summary_path = output_dir / "summary.json"
    detail.to_csv(detail_path, index=False)

    payload = {
        "schema_version": 1,
        "status": "V4_SHARES_RESIDUAL_DIAGNOSTIC_COMPLETE",
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

    print("V4 SHARES RESIDUAL DIAGNOSTIC")
    print(f"As of:                       {as_of}")
    print(f"Residual tickers:            {len(detail)}")
    print()
    for r in detail.itertuples(index=False):
        print(f"{r.ticker:6s} evidence_rows={r.evidence_rows}")
        print(f"       statuses={r.statuses or '-'}")
        print(f"       reasons={r.reasons or '-'}")
        print(f"       selection_rules={r.selection_rules or '-'}")
        print(f"       context={r.context_instants or '-'}")
        print(f"       accessions={r.accessions or '-'}")
        print(f"       accepted_at={r.accepted_at or '-'}")
        print(f"       component_counts={r.component_counts or '-'}")
        print(f"       component_dimensions={r.component_dimensions or '-'}")
        print(f"       component_values={r.component_values or '-'}")
        print(f"       candidate_values={r.candidate_values or '-'}")
        print(f"       canonical_values={r.canonical_values or '-'}")
        print(f"       validation_bands={r.validation_bands or '-'}")
    print()
    print(f"Detail:                      {detail_path}")
    print(f"Summary:                     {summary_path}")
    print("V1/V2/V3 INPUTS AND RULES WERE NOT MODIFIED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
