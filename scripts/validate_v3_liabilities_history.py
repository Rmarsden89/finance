from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import time

import pandas as pd

from finance.research.liabilities_historical_validation import (
    availability_timestamp,
    build_quarter_liabilities_comparisons,
)
from finance.research.v2 import resolve_v2_sec_artifact_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Historically validate the strict SEC LiabilitiesCurrent + "
            "LiabilitiesNoncurrent construction and replay its PIT availability "
            "against the frozen historical decision calendar."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("zip_dir", type=Path)
    parser.add_argument("--pattern", default="*.zip")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _yearly_summary(comparable: pd.DataFrame) -> pd.DataFrame:
    if comparable.empty:
        return pd.DataFrame(
            columns=[
                "year",
                "comparison_rows",
                "ciks",
                "exact_matches",
                "within_0_01_pct",
                "within_0_1_pct",
                "within_1_pct",
                "material_differences",
                "within_0_1_pct_rate",
                "max_absolute_relative_error",
            ]
        )
    frame = comparable.copy()
    frame["year"] = pd.to_datetime(
        frame["ddate_date"], errors="coerce"
    ).dt.year
    rows = []
    for year, group in frame.groupby("year", dropna=False):
        bands = group["validation_band"].value_counts()
        exact = int(bands.get("exact_match", 0))
        within_001 = int(bands.get("within_0_01_pct", 0))
        within_01 = int(bands.get("within_0_1_pct", 0))
        within_1 = int(bands.get("within_1_pct", 0))
        material = int(bands.get("material_difference", 0))
        total = len(group)
        rows.append(
            {
                "year": int(year) if pd.notna(year) else pd.NA,
                "comparison_rows": total,
                "ciks": int(group["cik"].nunique()),
                "exact_matches": exact,
                "within_0_01_pct": within_001,
                "within_0_1_pct": within_01,
                "within_1_pct": within_1,
                "material_differences": material,
                "within_0_1_pct_rate": (
                    float((exact + within_001 + within_01) / total)
                    if total
                    else None
                ),
                "max_absolute_relative_error": float(
                    group["absolute_relative_error"].max()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("year", kind="stable")


def _pit_replay(
    candidates: pd.DataFrame,
    panel: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty:
        return pd.DataFrame(), pd.DataFrame()

    panel = panel.copy()
    panel["decision_date"] = pd.to_datetime(
        panel["decision_date"], errors="coerce"
    ).dt.normalize()
    panel["cik"] = pd.to_numeric(panel["cik"], errors="coerce").astype("Int64")
    if "as_of" not in panel.columns:
        raise ValueError("Historical panel missing as_of for PIT replay")
    panel["_cutoff"] = panel["as_of"].map(availability_timestamp_from_panel)

    facts = candidates.loc[candidates["has_construction"]].copy()
    facts["cik"] = pd.to_numeric(facts["cik"], errors="coerce").astype("Int64")
    facts["_available_at"] = [
        availability_timestamp(a, f)
        for a, f in zip(facts["accepted_at"], facts["filed_date"])
    ]
    facts["_period"] = pd.to_datetime(
        facts["ddate_date"], errors="coerce"
    ).dt.normalize()

    facts_by_cik = {
        int(cik): group.sort_values(
            ["_available_at", "_period", "accepted_at"],
            kind="stable",
        ).reset_index(drop=True)
        for cik, group in facts.loc[facts["cik"].notna()].groupby("cik")
    }

    rows = []
    for row in panel[
        ["decision_date", "ticker", "cik", "_cutoff"]
    ].itertuples(index=False):
        if pd.isna(row.cik) or pd.isna(row._cutoff):
            continue
        group = facts_by_cik.get(int(row.cik))
        if group is None:
            continue
        eligible = group.loc[group["_available_at"].le(row._cutoff)]
        if eligible.empty:
            continue
        latest_period = eligible["_period"].max()
        latest = eligible.loc[eligible["_period"].eq(latest_period)].copy()
        latest = latest.sort_values(
            ["_available_at", "accepted_at"], kind="stable"
        )
        selected = latest.iloc[-1]
        rows.append(
            {
                "decision_date": row.decision_date,
                "ticker": row.ticker,
                "cik": int(row.cik),
                "decision_cutoff": row._cutoff,
                "selected_accession": selected["adsh"],
                "selected_period_date": selected["ddate_date"],
                "selected_accepted_at": selected["accepted_at"],
                "selected_available_at": selected["_available_at"],
                "constructed_liabilities": selected[
                    "constructed_liabilities"
                ],
                "direct_liabilities_if_present": selected["Liabilities"],
                "has_direct_control": bool(selected["has_direct"]),
                "source_zip": selected["source_zip"],
            }
        )

    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, pd.DataFrame()

    detail["decision_year"] = pd.to_datetime(
        detail["decision_date"], errors="coerce"
    ).dt.year
    summary = (
        detail.groupby("decision_year", as_index=False)
        .agg(
            replay_rows=("ticker", "size"),
            replay_tickers=("ticker", "nunique"),
            direct_control_rows=("has_direct_control", "sum"),
        )
        .sort_values("decision_year", kind="stable")
    )
    return detail, summary


def availability_timestamp_from_panel(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(
            "America/New_York",
            ambiguous="raise",
            nonexistent="raise",
        )
    return stamp.tz_convert("America/New_York")


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    zip_dir = args.zip_dir.resolve()
    zip_paths = sorted(zip_dir.glob(args.pattern))
    if not zip_paths:
        raise SystemExit(
            f"No SEC ZIPs found in {zip_dir} matching {args.pattern!r}"
        )

    started = time.monotonic()
    all_candidates: list[pd.DataFrame] = []
    all_comparable: list[pd.DataFrame] = []

    print("V3 HISTORICAL SEC LIABILITIES CONSTRUCTION VALIDATION", flush=True)
    print(f"As of:                       {args.as_of.isoformat()}", flush=True)
    print(f"SEC ZIP directory:           {zip_dir}", flush=True)
    print(f"SEC ZIPs:                    {len(zip_paths)}", flush=True)

    for index, path in enumerate(zip_paths, start=1):
        print(
            f"[{index}/{len(zip_paths)}] {path.name} "
            f"elapsed={(time.monotonic() - started)/60:.1f}m",
            flush=True,
        )
        candidates, comparable = build_quarter_liabilities_comparisons(path)
        if not candidates.empty:
            all_candidates.append(candidates)
        if not comparable.empty:
            all_comparable.append(comparable)
        print(
            f"    constructions={int(candidates['has_construction'].sum()) if not candidates.empty else 0:,} "
            f"controls={len(comparable):,}",
            flush=True,
        )

    candidates = (
        pd.concat(all_candidates, ignore_index=True)
        if all_candidates
        else pd.DataFrame()
    )
    comparable = (
        pd.concat(all_comparable, ignore_index=True)
        if all_comparable
        else pd.DataFrame()
    )

    if not comparable.empty:
        comparable = comparable.drop_duplicates(
            ["adsh", "cik", "ddate_date"],
            keep="last",
        ).reset_index(drop=True)
    if not candidates.empty:
        candidates = candidates.drop_duplicates(
            ["adsh", "cik", "ddate_date"],
            keep="last",
        ).reset_index(drop=True)

    yearly = _yearly_summary(comparable)
    bands = (
        comparable["validation_band"].value_counts().to_dict()
        if not comparable.empty
        else {}
    )
    total = len(comparable)
    exact = int(bands.get("exact_match", 0))
    within_001 = int(bands.get("within_0_01_pct", 0))
    within_01 = int(bands.get("within_0_1_pct", 0))
    within_1 = int(bands.get("within_1_pct", 0))
    material = int(bands.get("material_difference", 0))

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    manifest = json.loads(v2["manifest"].read_text(encoding="utf-8"))
    historical_value = manifest.get("input_artifacts", {}).get("historical panel")
    if not historical_value:
        raise SystemExit("V2 manifest lacks historical panel input")
    historical_path = Path(historical_value)
    if not historical_path.is_absolute():
        historical_path = root / historical_path
    if not historical_path.exists():
        raise SystemExit(f"Missing historical panel: {historical_path}")

    panel = pd.read_csv(
        historical_path,
        usecols=lambda c: c in {"decision_date", "as_of", "ticker", "cik"},
        low_memory=False,
    )
    replay_detail, replay_yearly = _pit_replay(candidates, panel)

    construction_rows = (
        int(candidates["has_construction"].sum())
        if not candidates.empty
        else 0
    )
    construction_ciks = (
        int(
            candidates.loc[
                candidates["has_construction"], "cik"
            ].nunique()
        )
        if not candidates.empty
        else 0
    )

    summary = {
        "as_of": args.as_of.isoformat(),
        "zip_count": len(zip_paths),
        "construction_filing_rows": construction_rows,
        "construction_ciks": construction_ciks,
        "comparison_rows": total,
        "comparison_ciks": (
            int(comparable["cik"].nunique()) if total else 0
        ),
        "exact_matches": exact,
        "within_0_01_pct": within_001,
        "within_0_1_pct": within_01,
        "within_1_pct": within_1,
        "material_differences": material,
        "within_0_1_pct_rate": (
            float((exact + within_001 + within_01) / total)
            if total
            else None
        ),
        "max_absolute_relative_error": (
            float(comparable["absolute_relative_error"].max())
            if total
            else None
        ),
        "historical_pit_replay_rows": len(replay_detail),
        "historical_pit_replay_tickers": (
            int(replay_detail["ticker"].nunique())
            if not replay_detail.empty
            else 0
        ),
        "historical_pit_replay_decision_dates": (
            int(replay_detail["decision_date"].nunique())
            if not replay_detail.empty
            else 0
        ),
        "availability_policy": (
            "max(sec_acceptance_timestamp, filed_date_0600_America_New_York)"
        ),
        "assets_minus_equity_used": False,
        "research_only": True,
        "model_inputs_modified": False,
        "paid_vendor_used": False,
    }

    output_dir = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / "liabilities_historical_validation"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    comparable_path = output_dir / "filing_overlap_validation.csv"
    yearly_path = output_dir / "filing_overlap_by_year.csv"
    replay_path = output_dir / "pit_replay_detail.csv"
    replay_yearly_path = output_dir / "pit_replay_by_year.csv"
    summary_path = output_dir / "summary.json"

    comparable.to_csv(comparable_path, index=False)
    yearly.to_csv(yearly_path, index=False)
    replay_detail.to_csv(replay_path, index=False)
    replay_yearly.to_csv(replay_yearly_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("V3 HISTORICAL LIABILITIES VALIDATION COMPLETE")
    print(f"Construction filing rows:    {construction_rows}")
    print(f"Construction CIKs:           {construction_ciks}")
    print(f"Comparable filing rows:      {total}")
    print(f"Comparable CIKs:             {summary['comparison_ciks']}")
    print(f"Exact matches:               {exact}")
    print(f"Within 0.01%:                {within_001}")
    print(f"Within 0.1%:                 {within_01}")
    print(f"Within 1%:                   {within_1}")
    print(f"Material differences:        {material}")
    print(
        f"Historical PIT replay rows:  "
        f"{summary['historical_pit_replay_rows']}"
    )
    print(
        f"PIT replay tickers/dates:    "
        f"{summary['historical_pit_replay_tickers']}/"
        f"{summary['historical_pit_replay_decision_dates']}"
    )
    print(f"Overlap detail:              {comparable_path}")
    print(f"Overlap by year:             {yearly_path}")
    print(f"PIT replay detail:           {replay_path}")
    print(f"PIT replay by year:          {replay_yearly_path}")
    print(f"Summary:                     {summary_path}")
    print("ASSETS - EQUITY WAS NOT USED.")
    print("NO PAID VENDOR WAS USED.")
    print("NO V1 OR V2 MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
