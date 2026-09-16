from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.data.sec_concepts import CANONICAL_TAGS


NAMESPACES = ("us-gaap", "dei")
DETAIL_COLUMNS = [
    "ticker",
    "company_name",
    "cik",
    "accession",
    "form",
    "report_date",
    "filing_date",
    "accepted_at",
    "discovery_status",
    "namespace_classification",
    "us_gaap_observation_count",
    "us_gaap_values",
    "us_gaap_tags",
    "dei_observation_count",
    "dei_values",
    "dei_tags",
    "snapshot_shares_outstanding",
    "snapshot_shares_present",
    "potential_missing_coverage_recovery",
    "reason",
    "companyfacts_path",
]
SUMMARY_COLUMNS = [
    "namespace_classification",
    "snapshot_share_status",
    "filings",
    "unique_ciks",
    "potential_missing_coverage_recoveries",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit exact-current-filing shares_outstanding observations across "
            "the us-gaap and dei CompanyFacts namespaces. Read-only."
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
        default=Path("reports/sec_current_share_namespace_audit.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/sec_current_share_namespace_summary.csv"),
    )
    return parser.parse_args()


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _number(value: object) -> float | None:
    result = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(result):
        return None
    return float(result)


def _format_values(values: set[float]) -> str:
    return "|".join(f"{value:.12g}" for value in sorted(values))


def inspect_share_namespaces(
    payload: dict,
    *,
    accession: str,
    report_date: str,
) -> dict:
    facts = payload.get("facts") or {}
    result: dict[str, object] = {}
    value_sets: dict[str, set[float]] = {}

    for namespace in NAMESPACES:
        observations: list[tuple[str, float]] = []
        namespace_facts = facts.get(namespace) or {}
        for tag in CANONICAL_TAGS["shares_outstanding"]:
            fact = namespace_facts.get(tag) or {}
            for uom, rows in (fact.get("units") or {}).items():
                if str(uom).strip().lower() != "shares":
                    continue
                for row in rows or []:
                    if _text(row.get("accn")) != accession:
                        continue
                    if _text(row.get("end"))[:10] != report_date:
                        continue
                    value = _number(row.get("val"))
                    if value is None or value <= 0:
                        continue
                    observations.append((tag, value))

        values = {value for _, value in observations}
        tags = {tag for tag, _ in observations}
        prefix = namespace.replace("-", "_")
        result[f"{prefix}_observation_count"] = len(observations)
        result[f"{prefix}_values"] = _format_values(values)
        result[f"{prefix}_tags"] = "|".join(sorted(tags))
        value_sets[namespace] = values

    us_gaap_values = value_sets["us-gaap"]
    dei_values = value_sets["dei"]
    if us_gaap_values and dei_values:
        classification = (
            "both_agree" if us_gaap_values == dei_values else "both_conflict"
        )
    elif us_gaap_values:
        classification = "us_gaap_only"
    elif dei_values:
        classification = "dei_only"
    else:
        classification = "neither"
    result["namespace_classification"] = classification
    return result


def _snapshot_lookup(snapshot: pd.DataFrame) -> tuple[dict[int, object], dict[str, object]]:
    by_cik: dict[int, object] = {}
    by_ticker: dict[str, object] = {}
    if snapshot.empty or "shares_outstanding" not in snapshot.columns:
        return by_cik, by_ticker

    for _, row in snapshot.iterrows():
        value = row.get("shares_outstanding", "")
        cik = _number(row.get("cik"))
        ticker = _text(row.get("ticker")).upper()
        if cik is not None:
            by_cik[int(cik)] = value
        if ticker:
            by_ticker[ticker] = value
    return by_cik, by_ticker


def build_namespace_audit(
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

    snapshot_by_cik, snapshot_by_ticker = _snapshot_lookup(current_snapshot)
    rows: list[dict] = []
    cutoff = as_of.normalize()

    for _, item in discovery.iterrows():
        ticker = _text(item.get("ticker")).upper()
        cik_number = _number(item.get("cik"))
        cik = int(cik_number) if cik_number is not None else None
        accession = _text(item.get("accession"))
        report_date = _text(item.get("report_date"))[:10]
        filing_date = pd.to_datetime(item.get("filing_date"), errors="coerce")
        companyfacts_path = (
            cache_dir / f"CIK{cik:010d}.json" if cik is not None else None
        )
        snapshot_value = (
            snapshot_by_cik.get(cik, snapshot_by_ticker.get(ticker, ""))
            if cik is not None
            else snapshot_by_ticker.get(ticker, "")
        )
        snapshot_number = _number(snapshot_value)
        snapshot_present = snapshot_number is not None and snapshot_number > 0

        row = {
            "ticker": ticker,
            "company_name": _text(item.get("company_name", item.get("name"))),
            "cik": cik if cik is not None else "",
            "accession": accession,
            "form": _text(item.get("form")),
            "report_date": report_date,
            "filing_date": _text(item.get("filing_date")),
            "accepted_at": _text(item.get("accepted_at")),
            "discovery_status": _text(item.get("status")),
            "namespace_classification": "",
            "us_gaap_observation_count": 0,
            "us_gaap_values": "",
            "us_gaap_tags": "",
            "dei_observation_count": 0,
            "dei_values": "",
            "dei_tags": "",
            "snapshot_shares_outstanding": snapshot_value,
            "snapshot_shares_present": snapshot_present,
            "potential_missing_coverage_recovery": False,
            "reason": "",
            "companyfacts_path": str(companyfacts_path or ""),
        }

        if pd.notna(filing_date) and filing_date.normalize() > cutoff:
            row.update(
                namespace_classification="filing_after_as_of",
                reason="Discovery filing date is after the requested as_of date.",
            )
        elif cik is None or not accession or not report_date:
            row.update(
                namespace_classification="invalid_discovery_identity",
                reason="Discovery row lacks a usable CIK, accession, or report date.",
            )
        elif companyfacts_path is None or not companyfacts_path.exists():
            row.update(
                namespace_classification="cache_missing",
                reason="Cached CompanyFacts JSON is missing.",
            )
        else:
            try:
                payload = json.loads(companyfacts_path.read_text(encoding="utf-8"))
                result = inspect_share_namespaces(
                    payload,
                    accession=accession,
                    report_date=report_date,
                )
                row.update(result)
            except (OSError, json.JSONDecodeError) as error:
                row.update(
                    namespace_classification="cache_error",
                    reason=f"Unable to read cached CompanyFacts JSON: {error}",
                )

        row["potential_missing_coverage_recovery"] = bool(
            row["namespace_classification"] == "dei_only" and not snapshot_present
        )
        rows.append(row)

    return pd.DataFrame(rows, columns=DETAIL_COLUMNS)


def summarize_namespace_audit(audit: pd.DataFrame) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    frame = audit.copy()
    frame["snapshot_share_status"] = frame["snapshot_shares_present"].map(
        {True: "present", False: "missing"}
    )
    summary = (
        frame.groupby(
            ["namespace_classification", "snapshot_share_status"],
            dropna=False,
        )
        .agg(
            filings=("ticker", "size"),
            unique_ciks=("cik", "nunique"),
            potential_missing_coverage_recoveries=(
                "potential_missing_coverage_recovery",
                "sum",
            ),
        )
        .reset_index()
        .sort_values(
            ["namespace_classification", "snapshot_share_status"],
            kind="stable",
        )
        .reset_index(drop=True)
    )
    return summary[SUMMARY_COLUMNS]


def main() -> None:
    args = parse_args()
    discovery = pd.read_csv(args.discovery, low_memory=False)
    snapshot = (
        pd.read_csv(args.current_snapshot, low_memory=False)
        if args.current_snapshot.exists()
        else pd.DataFrame()
    )
    audit = build_namespace_audit(
        discovery,
        snapshot,
        cache_dir=args.cache_dir,
        as_of=args.as_of,
    )
    summary = summarize_namespace_audit(audit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(args.output, index=False)
    summary.to_csv(args.summary_output, index=False)

    print("SEC CURRENT SHARE NAMESPACE AUDIT")
    print(f"Discovery rows:                 {len(audit)}")
    for classification, count in audit["namespace_classification"].value_counts().items():
        print(f"{classification:30} {count:>6}")
    recoveries = int(audit["potential_missing_coverage_recovery"].sum())
    print(f"Potential missing recoveries:   {recoveries}")
    print(f"Detail:                         {args.output}")
    print(f"Summary:                        {args.summary_output}")
    print("Canonical winners/cache were NOT modified.")


if __name__ == "__main__":
    main()
