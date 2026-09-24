from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.data.sec_concepts import CANONICAL_TAGS


DETAIL_COLUMNS = [
    "ticker",
    "company_name",
    "cik",
    "snapshot_shares_outstanding",
    "accession",
    "form",
    "report_date",
    "filing_date",
    "accepted_at",
    "company_classification",
    "taxonomy",
    "source_tag",
    "value",
    "measurement_date",
    "date_offset_days",
    "lag_bucket",
    "observation_classification",
    "bounded_candidate",
    "reason",
    "companyfacts_path",
]

SUMMARY_COLUMNS = [
    "company_classification",
    "companies",
    "unique_ciks",
    "observations",
    "exact_observations",
    "bounded_candidates",
    "offset_1_7",
    "offset_8_14",
    "offset_15_31",
    "offset_32_plus",
    "before_report",
    "after_acceptance",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit same-accession shares-outstanding observations whose instant "
            "date differs from the filing report date. Read-only."
        )
    )
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("reports/sec_current_filing_discovery.csv"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/sec/current/companyfacts"),
    )
    parser.add_argument(
        "--current-snapshot",
        type=Path,
        default=Path("reports/current_shadow_snapshot.csv"),
    )
    parser.add_argument("--as-of", type=pd.Timestamp, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_share_date_offset_audit.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/sec_share_date_offset_summary.csv"),
    )
    return parser.parse_args()


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _number(value: object) -> float | None:
    result = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(result) else float(result)


def _date(value: object) -> pd.Timestamp | None:
    result = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(result):
        return None
    return result.tz_localize(None).normalize()


def _lag_bucket(offset: int) -> str:
    if offset < 0:
        return "before_report"
    if offset == 0:
        return "exact"
    if offset <= 7:
        return "1_7"
    if offset <= 14:
        return "8_14"
    if offset <= 31:
        return "15_31"
    return "32_plus"


def inspect_date_offset_observations(
    payload: dict,
    *,
    accession: str,
    report_date: object,
    accepted_at: object,
    filing_date: object,
) -> list[dict]:
    """Return usable instant share observations from the exact filing accession."""

    report = _date(report_date)
    boundary = _date(accepted_at) or _date(filing_date)
    if report is None:
        return []

    rows: list[dict] = []
    facts = payload.get("facts") or {}
    for taxonomy in ("us-gaap", "dei"):
        namespace = facts.get(taxonomy) or {}
        for tag in CANONICAL_TAGS["shares_outstanding"]:
            fact = namespace.get(tag) or {}
            for uom, observations in (fact.get("units") or {}).items():
                if _text(uom).lower() != "shares":
                    continue
                for observation in observations or []:
                    if _text(observation.get("accn")) != accession:
                        continue
                    if _text(observation.get("start")):
                        continue
                    value = _number(observation.get("val"))
                    measurement = _date(observation.get("end"))
                    if value is None or value <= 0 or measurement is None:
                        continue

                    offset = int((measurement - report).days)
                    bounded = bool(offset > 0 and boundary is not None and measurement <= boundary)
                    if offset == 0:
                        classification = "exact_report_date"
                    elif offset < 0:
                        classification = "before_report"
                    elif bounded:
                        classification = "after_report_before_or_on_acceptance"
                    else:
                        classification = "after_acceptance"

                    rows.append(
                        {
                            "taxonomy": taxonomy,
                            "source_tag": tag,
                            "value": value,
                            "measurement_date": measurement.date().isoformat(),
                            "date_offset_days": offset,
                            "lag_bucket": _lag_bucket(offset),
                            "observation_classification": classification,
                            "bounded_candidate": bounded,
                        }
                    )
    return rows


def classify_company(observations: list[dict]) -> str:
    if any(row["observation_classification"] == "exact_report_date" for row in observations):
        return "exact_candidate_present"
    bounded = [row for row in observations if row["bounded_candidate"]]
    if len(bounded) == 1:
        return "single_bounded_cover_candidate"
    if bounded:
        values = {float(row["value"]) for row in bounded}
        return (
            "multiple_same_value_bounded_candidates"
            if len(values) == 1
            else "conflicting_bounded_candidates"
        )
    if observations:
        return "same_accession_unbounded_only"
    return "no_same_accession_share_observation"


def _current_missing_companies(snapshot: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "cik", "shares_outstanding"}
    missing = required - set(snapshot.columns)
    if missing:
        raise ValueError(f"Current snapshot missing required columns: {sorted(missing)}")
    numeric = pd.to_numeric(snapshot["shares_outstanding"], errors="coerce")
    return snapshot.loc[numeric.isna() | numeric.le(0)].copy()


def _discovery_match(
    discovery: pd.DataFrame,
    *,
    ticker: str,
    cik: int | None,
    as_of: pd.Timestamp,
) -> pd.Series | None:
    matches = pd.DataFrame()
    if cik is not None:
        matches = discovery.loc[pd.to_numeric(discovery["cik"], errors="coerce").eq(cik)]
    if matches.empty:
        matches = discovery.loc[
            discovery["ticker"].fillna("").astype(str).str.upper().eq(ticker)
        ]
    if matches.empty:
        return None

    matches = matches.copy()
    matches["_filing_date"] = pd.to_datetime(matches.get("filing_date"), errors="coerce")
    matches = matches.loc[
        matches["_filing_date"].isna()
        | matches["_filing_date"].dt.normalize().le(as_of.normalize())
    ]
    if matches.empty:
        return None
    status_priority = matches.get("status", pd.Series("", index=matches.index)).eq(
        "new_filing_cached"
    )
    matches["_status_priority"] = status_priority.astype(int)
    matches = matches.sort_values(
        ["_status_priority", "_filing_date"], ascending=[False, False], na_position="last"
    )
    return matches.iloc[0]


def build_date_offset_audit(
    discovery: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    *,
    cache_dir: Path,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    required = {"ticker", "cik", "accession", "report_date"}
    missing = required - set(discovery.columns)
    if missing:
        raise ValueError(f"Discovery missing required columns: {sorted(missing)}")

    result: list[dict] = []
    for _, company in _current_missing_companies(current_snapshot).iterrows():
        ticker = _text(company.get("ticker")).upper()
        cik_value = _number(company.get("cik"))
        cik = int(cik_value) if cik_value is not None else None
        base = {
            "ticker": ticker,
            "company_name": _text(company.get("company_name", company.get("name"))),
            "cik": cik if cik is not None else "",
            "snapshot_shares_outstanding": company.get("shares_outstanding", ""),
            "accession": "",
            "form": "",
            "report_date": "",
            "filing_date": "",
            "accepted_at": "",
            "company_classification": "",
            "taxonomy": "",
            "source_tag": "",
            "value": "",
            "measurement_date": "",
            "date_offset_days": "",
            "lag_bucket": "",
            "observation_classification": "",
            "bounded_candidate": False,
            "reason": "",
            "companyfacts_path": "",
        }
        filing = _discovery_match(
            discovery, ticker=ticker, cik=cik, as_of=as_of
        )
        if filing is None:
            result.append(
                {
                    **base,
                    "company_classification": "no_current_discovery",
                    "reason": "No current discovery row matched by CIK or ticker.",
                }
            )
            continue

        accession = _text(filing.get("accession"))
        report_date = _text(filing.get("report_date"))[:10]
        cache_path = cache_dir / f"CIK{cik:010d}.json" if cik is not None else None
        base.update(
            accession=accession,
            form=_text(filing.get("form")),
            report_date=report_date,
            filing_date=_text(filing.get("filing_date")),
            accepted_at=_text(filing.get("accepted_at")),
            companyfacts_path=str(cache_path or ""),
        )
        if cache_path is None or not cache_path.exists():
            result.append(
                {
                    **base,
                    "company_classification": "cache_missing",
                    "reason": "Cached CompanyFacts JSON is missing.",
                }
            )
            continue

        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result.append(
                {
                    **base,
                    "company_classification": "cache_error",
                    "reason": f"Unable to read cached CompanyFacts JSON: {error}",
                }
            )
            continue

        observations = inspect_date_offset_observations(
            payload,
            accession=accession,
            report_date=report_date,
            accepted_at=filing.get("accepted_at"),
            filing_date=filing.get("filing_date"),
        )
        classification = classify_company(observations)
        if not observations:
            result.append({**base, "company_classification": classification})
        else:
            result.extend(
                {**base, **observation, "company_classification": classification}
                for observation in observations
            )

    return pd.DataFrame(result, columns=DETAIL_COLUMNS)


def summarize_date_offset_audit(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    rows: list[dict] = []
    for classification, group in audit.groupby("company_classification", dropna=False):
        observations = group["observation_classification"].fillna("").ne("")
        company_keys = group["cik"].astype(str) + "|" + group["ticker"].astype(str)
        rows.append(
            {
                "company_classification": classification,
                "companies": int(company_keys.nunique()),
                "unique_ciks": int(group.loc[group["cik"].astype(str).ne(""), "cik"].nunique()),
                "observations": int(observations.sum()),
                "exact_observations": int(group["observation_classification"].eq("exact_report_date").sum()),
                "bounded_candidates": int(group["bounded_candidate"].fillna(False).astype(bool).sum()),
                "offset_1_7": int(group["lag_bucket"].eq("1_7").sum()),
                "offset_8_14": int(group["lag_bucket"].eq("8_14").sum()),
                "offset_15_31": int(group["lag_bucket"].eq("15_31").sum()),
                "offset_32_plus": int(group["lag_bucket"].eq("32_plus").sum()),
                "before_report": int(group["observation_classification"].eq("before_report").sum()),
                "after_acceptance": int(group["observation_classification"].eq("after_acceptance").sum()),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).sort_values(
        "company_classification", kind="stable"
    ).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    discovery = pd.read_csv(args.discovery, low_memory=False)
    snapshot = pd.read_csv(args.current_snapshot, low_memory=False)
    audit = build_date_offset_audit(
        discovery,
        snapshot,
        cache_dir=args.cache_dir,
        as_of=args.as_of,
    )
    summary = summarize_date_offset_audit(audit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(args.output, index=False)
    summary.to_csv(args.summary_output, index=False)

    companies = audit[["ticker", "cik"]].drop_duplicates()
    print("SEC SHARE DATE-OFFSET AUDIT")
    print(f"Missing-share companies:        {len(companies)}")
    for _, row in summary.iterrows():
        print(f"{row['company_classification']:40} {int(row['companies']):>6}")
    print(f"Bounded observations:           {int(audit['bounded_candidate'].sum())}")
    print(f"Detail:                         {args.output}")
    print(f"Summary:                        {args.summary_output}")
    print("Canonical winners/cache were NOT modified.")


if __name__ == "__main__":
    main()
