from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.data.sec_concepts import CANONICAL_TAGS


TRACE_COLUMNS = [
    "stage",
    "status",
    "ticker",
    "cik",
    "accession",
    "concept",
    "taxonomy",
    "source_tag",
    "value",
    "uom",
    "form",
    "period_date",
    "filed_date",
    "accepted_at",
    "reason",
    "source_path",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace one company's shares_outstanding evidence through the current "
            "SEC CompanyFacts, shadow-merge, and current-snapshot path. Read-only."
        )
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--cik", type=int, required=True)
    parser.add_argument("--as-of", type=pd.Timestamp, required=True)
    parser.add_argument(
        "--companyfacts",
        type=Path,
        help=(
            "Cached CompanyFacts JSON. Default: "
            "data/cache/sec/current/companyfacts/CIK##########.json"
        ),
    )
    parser.add_argument(
        "--discovery",
        type=Path,
        default=Path("reports/sec_current_filing_discovery.csv"),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("reports/sec_current_candidate_facts.csv"),
    )
    parser.add_argument(
        "--merge-audit",
        type=Path,
        default=Path("reports/sec_shadow_merge_audit.csv"),
    )
    parser.add_argument(
        "--shadow-winners",
        type=Path,
        default=Path("data/cache/sec/shadow/sec_winner_facts_shadow.csv"),
    )
    parser.add_argument(
        "--current-snapshot",
        type=Path,
        default=Path("reports/current_shadow_snapshot.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_current_share_trace.csv"),
    )
    return parser.parse_args()


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _number(value: object) -> float | None:
    result = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(result) else float(result)


def _base_row(stage: str, status: str, ticker: str, cik: int) -> dict:
    row = {column: "" for column in TRACE_COLUMNS}
    row.update(stage=stage, status=status, ticker=ticker, cik=cik)
    return row


def _matching_rows(frame: pd.DataFrame, *, ticker: str, cik: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    mask = pd.Series(False, index=frame.index)
    if "ticker" in frame.columns:
        mask |= frame["ticker"].fillna("").astype(str).str.upper().eq(ticker.upper())
    if "cik" in frame.columns:
        mask |= pd.to_numeric(frame["cik"], errors="coerce").eq(cik)
    return frame.loc[mask].copy()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def companyfacts_rows(payload: dict, *, ticker: str, cik: int, as_of: pd.Timestamp) -> list[dict]:
    rows: list[dict] = []
    taxonomies = payload.get("facts") or {}
    for taxonomy in ("us-gaap", "dei"):
        taxonomy_facts = taxonomies.get(taxonomy) or {}
        for tag in CANONICAL_TAGS["shares_outstanding"]:
            fact = taxonomy_facts.get(tag) or {}
            for uom, observations in (fact.get("units") or {}).items():
                for observation in observations or []:
                    filed = pd.to_datetime(observation.get("filed"), errors="coerce")
                    if pd.notna(filed) and filed.normalize() > as_of.normalize():
                        continue
                    row = _base_row("companyfacts", "observation", ticker, cik)
                    row.update(
                        accession=_text(observation.get("accn")),
                        concept="shares_outstanding",
                        taxonomy=taxonomy,
                        source_tag=tag,
                        value=observation.get("val", ""),
                        uom=uom,
                        form=_text(observation.get("form")),
                        period_date=_text(observation.get("end")),
                        filed_date=_text(observation.get("filed")),
                    )
                    rows.append(row)
    return rows


def build_current_share_trace(
    *,
    payload: dict | None,
    discovery: pd.DataFrame,
    candidates: pd.DataFrame,
    merge_audit: pd.DataFrame,
    shadow_winners: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    ticker: str,
    cik: int,
    as_of: pd.Timestamp,
    source_paths: dict[str, str] | None = None,
) -> pd.DataFrame:
    paths = source_paths or {}
    rows: list[dict] = []

    if payload is None:
        row = _base_row("companyfacts", "source_missing", ticker, cik)
        row["source_path"] = paths.get("companyfacts", "")
        rows.append(row)
    else:
        evidence = companyfacts_rows(payload, ticker=ticker, cik=cik, as_of=as_of)
        if not evidence:
            evidence = [_base_row("companyfacts", "no_share_observations", ticker, cik)]
        for row in evidence:
            row["source_path"] = paths.get("companyfacts", "")
        rows.extend(evidence)

    discovery_rows = _matching_rows(discovery, ticker=ticker, cik=cik)
    if discovery_rows.empty:
        rows.append(_base_row("discovery", "no_matching_row", ticker, cik))
    else:
        for _, item in discovery_rows.iterrows():
            row = _base_row("discovery", _text(item.get("status")) or "found", ticker, cik)
            row.update(
                accession=_text(item.get("accession")),
                form=_text(item.get("form")),
                period_date=_text(item.get("report_date")),
                filed_date=_text(item.get("filing_date")),
                accepted_at=_text(item.get("accepted_at")),
                reason=_text(item.get("error")),
                source_path=paths.get("discovery", ""),
            )
            rows.append(row)

    candidate_rows = _matching_rows(candidates, ticker=ticker, cik=cik)
    if "concept" in candidate_rows.columns:
        candidate_rows = candidate_rows.loc[
            candidate_rows["concept"].astype(str).eq("shares_outstanding")
        ]
    if candidate_rows.empty:
        rows.append(_base_row("candidate_extraction", "no_share_candidate", ticker, cik))
    else:
        for _, item in candidate_rows.iterrows():
            row = _base_row("candidate_extraction", "candidate", ticker, cik)
            row.update(
                accession=_text(item.get("adsh")),
                concept=_text(item.get("concept")),
                source_tag=_text(item.get("source_tag", item.get("tag"))),
                value=item.get("value", ""),
                uom=_text(item.get("uom")),
                form=_text(item.get("form")),
                period_date=_text(item.get("ddate_date")),
                filed_date=_text(item.get("filed_date")),
                accepted_at=_text(item.get("accepted_at")),
                source_path=paths.get("candidates", ""),
            )
            rows.append(row)

    audit_rows = _matching_rows(merge_audit, ticker=ticker, cik=cik)
    if "concept" in audit_rows.columns:
        audit_rows = audit_rows.loc[
            audit_rows["concept"].astype(str).eq("shares_outstanding")
        ]
    if audit_rows.empty:
        rows.append(_base_row("shadow_merge", "no_share_audit_row", ticker, cik))
    else:
        for _, item in audit_rows.iterrows():
            row = _base_row("shadow_merge", _text(item.get("status")) or "found", ticker, cik)
            row.update(
                accession=_text(item.get("adsh")),
                concept=_text(item.get("concept")),
                source_tag=_text(item.get("source_tag")),
                value=item.get("value", ""),
                uom=_text(item.get("uom")),
                period_date=_text(item.get("ddate_date")),
                accepted_at=_text(item.get("accepted_at")),
                reason=_text(item.get("reason")),
                source_path=paths.get("merge_audit", ""),
            )
            rows.append(row)

    winner_rows = _matching_rows(shadow_winners, ticker=ticker, cik=cik)
    if "concept" in winner_rows.columns:
        winner_rows = winner_rows.loc[
            winner_rows["concept"].astype(str).eq("shares_outstanding")
        ]
    if winner_rows.empty:
        rows.append(_base_row("shadow_winners", "no_share_winner", ticker, cik))
    else:
        for _, item in winner_rows.iterrows():
            row = _base_row("shadow_winners", "winner", ticker, cik)
            row.update(
                accession=_text(item.get("adsh")),
                concept=_text(item.get("concept")),
                source_tag=_text(item.get("source_tag")),
                value=item.get("value", ""),
                uom=_text(item.get("uom")),
                form=_text(item.get("form")),
                period_date=_text(item.get("ddate_date")),
                filed_date=_text(item.get("filed_date")),
                accepted_at=_text(item.get("accepted_at")),
                source_path=paths.get("shadow_winners", ""),
            )
            rows.append(row)

    snapshot_rows = _matching_rows(current_snapshot, ticker=ticker, cik=cik)
    if snapshot_rows.empty:
        rows.append(_base_row("current_snapshot", "company_missing", ticker, cik))
    else:
        for _, item in snapshot_rows.iterrows():
            value = _number(item.get("shares_outstanding"))
            row = _base_row(
                "current_snapshot",
                "share_value_present" if value is not None and value > 0 else "share_value_missing",
                ticker,
                cik,
            )
            row.update(
                accession=_text(item.get("shares_outstanding_adsh")),
                concept="shares_outstanding",
                source_tag=_text(item.get("shares_outstanding_source_tag")),
                value="" if value is None else value,
                form=_text(item.get("shares_outstanding_form")),
                period_date=_text(item.get("shares_outstanding_period_date")),
                accepted_at=_text(item.get("shares_outstanding_accepted_at")),
                source_path=paths.get("current_snapshot", ""),
            )
            rows.append(row)

    return pd.DataFrame(rows, columns=TRACE_COLUMNS)


def main() -> None:
    args = parse_args()
    companyfacts_path = args.companyfacts or Path(
        f"data/cache/sec/current/companyfacts/CIK{args.cik:010d}.json"
    )
    payload = (
        json.loads(companyfacts_path.read_text(encoding="utf-8"))
        if companyfacts_path.exists()
        else None
    )
    paths = {
        "companyfacts": str(companyfacts_path),
        "discovery": str(args.discovery),
        "candidates": str(args.candidates),
        "merge_audit": str(args.merge_audit),
        "shadow_winners": str(args.shadow_winners),
        "current_snapshot": str(args.current_snapshot),
    }
    trace = build_current_share_trace(
        payload=payload,
        discovery=_read_csv(args.discovery),
        candidates=_read_csv(args.candidates),
        merge_audit=_read_csv(args.merge_audit),
        shadow_winners=_read_csv(args.shadow_winners),
        current_snapshot=_read_csv(args.current_snapshot),
        ticker=args.ticker,
        cik=args.cik,
        as_of=args.as_of,
        source_paths=paths,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    trace.to_csv(args.output, index=False)

    print("SEC CURRENT SHARES TRACE")
    print(f"Ticker:                    {args.ticker.upper()}")
    print(f"CIK:                       {args.cik}")
    print(f"As of:                     {args.as_of.date().isoformat()}")
    for stage, group in trace.groupby("stage", sort=False):
        statuses = ", ".join(
            f"{status}={count}"
            for status, count in group["status"].value_counts().items()
        )
        print(f"{stage:26}{statuses}")
    print(f"Output:                    {args.output}")
    print("SEC caches, winners, snapshots, and V1 were NOT modified.")


if __name__ == "__main__":
    main()
