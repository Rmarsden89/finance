from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from finance.data.sources.sec_financial_statements import load_sec_financial_statement_zip


COMMON_STOCK_TAGS = {
    "CommonStockSharesOutstanding",
    "EntityCommonStockSharesOutstanding",
}
EXACT_SEGMENT = "EquityComponents=CommonStock;"
SAFE_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Shadow audit of SEC share facts for current-universe names missing "
            "canonical shares_outstanding. Does not modify winners or caches."
        )
    )
    parser.add_argument("--current-snapshot", type=Path, required=True)
    parser.add_argument("zip_dir", type=Path)
    parser.add_argument("--pattern", default="*.zip")
    parser.add_argument("--as-of", type=pd.Timestamp, required=True)
    parser.add_argument("--detail-output", type=Path, default=Path("reports/missing_shares_current_detail.csv"))
    parser.add_argument("--case-output", type=Path, default=Path("reports/missing_shares_current_cases.csv"))
    parser.add_argument("--summary-output", type=Path, default=Path("reports/missing_shares_current_summary.csv"))
    parser.add_argument("--universe-column", default="ticker")
    return parser.parse_args()


def clean(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def load_missing_universe(path: Path, ticker_column: str) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    required = {ticker_column, "cik"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Current snapshot missing required columns: {sorted(missing)}")

    shares = pd.to_numeric(frame.get("shares_outstanding"), errors="coerce")
    frame["_shares_present"] = shares.notna() & shares.gt(0)
    missing_frame = frame.loc[~frame["_shares_present"]].copy()
    missing_frame["cik"] = pd.to_numeric(missing_frame["cik"], errors="coerce").astype("Int64")
    missing_frame["cik_int"] = missing_frame["cik"]
    return missing_frame


def classify_case(
    missing_row: pd.Series,
    facts: pd.DataFrame,
    as_of: pd.Timestamp,
) -> dict:
    cik_value = missing_row.get("cik_int")
    if pd.isna(cik_value):
        return {
            "ticker": missing_row.get("ticker", ""),
            "company_name": missing_row.get("company_name", ""),
            "cik": "",
            "snapshot_shares_outstanding": missing_row.get("shares_outstanding", ""),
            "fact_rows_seen": 0,
            "classification": "identity_history_issue",
            "proposed_rule_candidate": False,
            "reason": "Current snapshot has no usable CIK mapping for SEC evidence lookup.",
        }

    base = {
        "ticker": missing_row.get("ticker", ""),
        "company_name": missing_row.get("company_name", ""),
        "cik": int(cik_value),
        "snapshot_shares_outstanding": missing_row.get("shares_outstanding", ""),
        "fact_rows_seen": len(facts),
    }
    if facts.empty:
        base.update(
            classification="no_usable_sec_evidence",
            proposed_rule_candidate=False,
            reason="No supported share facts found for the mapped CIK.",
        )
        return base

    facts = facts.copy()
    facts["value_num"] = pd.to_numeric(facts["value"], errors="coerce")
    facts["accepted_at"] = pd.to_datetime(facts["accepted_at"], errors="coerce")
    facts["ddate_date"] = pd.to_datetime(facts["ddate_date"], errors="coerce")
    eligible = facts.loc[
        facts["accepted_at"].notna()
        & (facts["accepted_at"] <= as_of)
        & facts["ddate_date"].notna()
        & facts["value_num"].notna()
        & facts["value_num"].gt(0)
        & clean(facts["form"]).isin(SAFE_FORMS)
    ].copy()

    if eligible.empty:
        base.update(
            classification="no_usable_sec_evidence",
            proposed_rule_candidate=False,
            reason="Facts exist, but none are positive, numeric, supported-form, and available by as_of.",
        )
        return base

    latest_date = eligible["ddate_date"].max()
    latest = eligible.loc[eligible["ddate_date"].eq(latest_date)].copy()
    latest_values = sorted(set(latest["value_num"].astype(float)))
    blank = latest.loc[clean(latest["segments"]).eq("")]
    exact = latest.loc[clean(latest["segments"]).eq(EXACT_SEGMENT)]
    other_dim = latest.loc[
        ~clean(latest["segments"]).isin(["", EXACT_SEGMENT])
    ]
    blank_values = sorted(set(blank["value_num"].astype(float)))
    exact_values = sorted(set(exact["value_num"].astype(float)))

    conflicting_values = sorted(set(latest["value_num"].astype(float)))
    base.update(
        fact_end_date=latest_date.date().isoformat(),
        latest_fact_rows=len(latest),
        latest_values="|".join(f"{v:.12g}" for v in latest_values),
        non_dimensional_values="|".join(f"{v:.12g}" for v in blank_values),
        exact_common_stock_values="|".join(f"{v:.12g}" for v in exact_values),
        other_dimensional_rows=len(other_dim),
        latest_forms="|".join(sorted(set(clean(latest["form"])))),
        latest_accessions="|".join(sorted(set(clean(latest["adsh"])))),
        latest_accepted_at="|".join(
            sorted(set(latest["accepted_at"].dt.strftime("%Y-%m-%dT%H:%M:%S").dropna()))
        ),
        conflicting_values="|".join(f"{v:.12g}" for v in conflicting_values),
    )

    one_exact_value = len(exact_values) == 1
    no_blank_conflict = not blank_values
    no_other_dimensions = other_dim.empty
    exact_has_supported_unit = clean(exact["uom"]).str.lower().eq("shares").all()
    direct_has_supported_unit = clean(blank["uom"]).str.lower().eq("shares").all()

    if one_exact_value and no_blank_conflict and no_other_dimensions and exact_has_supported_unit:
        base.update(
            classification="safe_alternate_candidate",
            proposed_rule_candidate=True,
            reason=(
                "Latest eligible evidence has one positive exact EquityComponents=CommonStock "
                "value, no blank or other dimensions, and shares unit."
            ),
        )
    elif blank_values and exact_values and set(blank_values) != set(exact_values):
        base.update(
            classification="ambiguous_conflicting",
            proposed_rule_candidate=False,
            reason="Latest eligible blank and exact-dimensional values conflict.",
        )
    elif len(conflicting_values) > 1 or not other_dim.empty:
        base.update(
            classification="ambiguous_conflicting",
            proposed_rule_candidate=False,
            reason="Multiple latest values or additional dimensions remain.",
        )
    elif not direct_has_supported_unit and blank_values:
        base.update(
            classification="ambiguous_conflicting",
            proposed_rule_candidate=False,
            reason="Non-dimensional evidence uses an unsupported unit.",
        )
    else:
        base.update(
            classification="other",
            proposed_rule_candidate=False,
            reason="SEC evidence exists but does not satisfy the conservative alternate-candidate rule.",
        )
    return base


def main() -> None:
    args = parse_args()
    started = time.monotonic()
    missing = load_missing_universe(args.current_snapshot, args.universe_column)
    print("MISSING CURRENT SHARES SEC AUDIT", flush=True)
    print(f"Current missing names with CIK: {len(missing):,}", flush=True)
    print(f"SEC ZIP directory: {args.zip_dir}", flush=True)
    print(f"As of: {args.as_of}", flush=True)

    ciks = set(missing.loc[missing["cik_int"].notna(), "cik_int"].astype(int))
    detail_parts: list[pd.DataFrame] = []
    zip_paths = sorted(args.zip_dir.glob(args.pattern))
    if not zip_paths:
        raise SystemExit(f"No SEC ZIPs found in {args.zip_dir} matching {args.pattern!r}")

    for index, path in enumerate(zip_paths, start=1):
        print(
            f"[{index}/{len(zip_paths)}] Loading {path.name} "
            f"elapsed={(time.monotonic() - started)/60:.1f}m",
            flush=True,
        )
        quarter = load_sec_financial_statement_zip(path)
        num = quarter.numeric_facts.copy()
        num["cik"] = num["adsh"].map(
            quarter.submissions.set_index("adsh")["cik"].to_dict()
        )
        num["cik"] = pd.to_numeric(num["cik"], errors="coerce")
        tag = clean(num["tag"])
        cik = pd.to_numeric(num["cik"], errors="coerce")
        candidate = num.loc[
            cik.isin(ciks)
            & tag.isin(COMMON_STOCK_TAGS)
        ].copy()
        if candidate.empty:
            continue
        candidate = candidate.merge(
            quarter.submissions[
                ["adsh", "cik", "name", "form", "period_date", "filed_date", "accepted_at"]
            ],
            on="adsh",
            how="left",
            suffixes=("", "_submission"),
            validate="many_to_one",
        )
        candidate["source_zip"] = path.name
        candidate["segments_clean"] = clean(candidate.get(
            "segments", pd.Series("", index=candidate.index)
        ))
        detail_parts.append(candidate)
        print(f"    matching share rows={len(candidate):,}", flush=True)

    detail = pd.concat(detail_parts, ignore_index=True) if detail_parts else pd.DataFrame()
    if not detail.empty:
        detail["accepted_at"] = pd.to_datetime(detail["accepted_at"], errors="coerce")
        detail["ddate_date"] = pd.to_datetime(detail["ddate_date"], errors="coerce")
        detail["available_by_as_of"] = detail["accepted_at"].le(args.as_of)
        detail["fact_period_matches_filing_period"] = detail["ddate_date"].eq(
            pd.to_datetime(detail["period_date"], errors="coerce")
        )
        detail["segment_class"] = "other_dimensional"
        detail.loc[detail["segments_clean"].eq(""), "segment_class"] = "non_dimensional"
        detail.loc[detail["segments_clean"].eq(EXACT_SEGMENT), "segment_class"] = "exact_common_stock"
        detail["value_num"] = pd.to_numeric(detail["value"], errors="coerce")
    else:
        detail = pd.DataFrame(columns=["cik"])

    cases = []
    for index, row in enumerate(missing.itertuples(index=False), start=1):
        cik = int(row.cik_int) if pd.notna(row.cik_int) else None
        facts = (
            detail.loc[detail["cik"].eq(cik)].copy()
            if cik is not None and not detail.empty
            else pd.DataFrame()
        )
        result = classify_case(pd.Series(row._asdict()), facts, args.as_of)
        cases.append(result)
        if index == 1 or index % 25 == 0 or index == len(missing):
            print(
                f"  Classified {index:,}/{len(missing):,} "
                f"elapsed={(time.monotonic() - started)/60:.1f}m",
                flush=True,
            )

    case_frame = pd.DataFrame(cases)
    summary = (
        case_frame.groupby(["classification", "proposed_rule_candidate"], dropna=False)
        .agg(cases=("cik", "size"), unique_ciks=("cik", "nunique"))
        .reset_index()
        .sort_values(["classification", "proposed_rule_candidate"])
    )
    args.detail_output.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(args.detail_output, index=False)
    case_frame.to_csv(args.case_output, index=False)
    summary.to_csv(args.summary_output, index=False)

    print()
    print("AUDIT COMPLETE", flush=True)
    print(f"Missing names analyzed:     {len(missing):,}", flush=True)
    print(f"Rows of SEC evidence:        {len(detail):,}", flush=True)
    print(f"Cases classified:            {len(case_frame):,}", flush=True)
    print(f"Shadow candidates:           {int(case_frame['proposed_rule_candidate'].sum()):,}", flush=True)
    print(f"Detail output:               {args.detail_output}", flush=True)
    print(f"Case output:                 {args.case_output}", flush=True)
    print(f"Summary output:              {args.summary_output}", flush=True)
    print("Canonical winners/cache were NOT modified.", flush=True)


if __name__ == "__main__":
    main()
