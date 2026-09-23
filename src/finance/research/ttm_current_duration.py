from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from finance.data.sec_concepts import CANONICAL_TAGS
from finance.data.sec_current_facts import load_companyfacts


_ALLOWED_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}

_TTM_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_cash_flow",
    "capital_expenditures",
}
_INCOME_CONCEPTS = {"revenue", "net_income"}
_CASH_FLOW_CONCEPTS = {"operating_cash_flow", "capital_expenditures"}
_FP_TO_YTD_QTRS = {"Q1": 1, "Q2": 2, "Q3": 3}


@dataclass(frozen=True)
class CurrentTtmDurationAudit:
    concepts_seen: int
    source_rows_seen: int
    rows_matching_accession: int
    rows_exact_report_date: int
    rows_period_eligible: int
    rows_numeric_value: int
    rows_output: int


def _tag_to_concept() -> dict[str, str]:
    result: dict[str, str] = {}
    for concept, tags in CANONICAL_TAGS.items():
        if concept not in _TTM_CONCEPTS:
            continue
        for tag in tags:
            result[tag] = concept
    return result


def _duration_qtrs(start: date | None, end: date) -> int | None:
    if start is None:
        return None
    days = (end - start).days + 1
    if 70 <= days <= 110:
        return 1
    if 150 <= days <= 210:
        return 2
    if 235 <= days <= 300:
        return 3
    if 330 <= days <= 390:
        return 4
    return None


def _eligible_representation(
    *,
    concept: str,
    form: str,
    fp: str,
    qtrs: int | None,
) -> bool:
    if qtrs is None or form not in _ALLOWED_FORMS:
        return False
    if form in _ANNUAL_FORMS:
        return qtrs == 4
    if form not in _QUARTERLY_FORMS:
        return False

    expected_ytd = _FP_TO_YTD_QTRS.get(fp)
    if concept in _INCOME_CONCEPTS:
        return qtrs == 1 or (
            fp in {"Q2", "Q3"} and qtrs == expected_ytd
        )
    if concept in _CASH_FLOW_CONCEPTS:
        return expected_ytd is not None and qtrs == expected_ytd
    return False


def extract_current_ttm_duration_candidates(
    payload: dict,
    *,
    ticker: str,
    accession: str,
    cik: int,
    company_name: str,
    form: str,
    report_date: date,
    filed_date: date,
    accepted_at: str,
) -> tuple[pd.DataFrame, CurrentTtmDurationAudit]:
    """Extract V2-only TTM duration representations from CompanyFacts.

    Unlike the frozen V1 current candidate layer, this retains Q2/Q3 YTD
    revenue and net-income facts needed by the frozen YTD-preferred TTM policy.
    Only observations for the exact filing accession and report date are kept.
    """

    tag_map = _tag_to_concept()
    facts = (payload.get("facts") or {}).get("us-gaap") or {}

    concepts_seen = 0
    source_rows_seen = 0
    accession_rows = 0
    exact_report_rows = 0
    eligible_rows = 0
    numeric_rows = 0
    rows: list[dict[str, object]] = []

    for tag, fact_payload in facts.items():
        concept = tag_map.get(tag)
        if concept is None:
            continue
        concepts_seen += 1

        for uom, observations in (fact_payload.get("units") or {}).items():
            for observation in observations or []:
                source_rows_seen += 1
                if str(observation.get("accn") or "") != accession:
                    continue
                accession_rows += 1

                raw_end = observation.get("end")
                raw_start = observation.get("start")
                if not raw_end or not raw_start:
                    continue
                try:
                    end_date = date.fromisoformat(str(raw_end)[:10])
                    start_date = date.fromisoformat(str(raw_start)[:10])
                except ValueError:
                    continue

                if end_date != report_date:
                    continue
                exact_report_rows += 1

                obs_form = str(observation.get("form") or form)
                fp = str(observation.get("fp") or "").upper().strip()
                fy = pd.to_numeric(
                    pd.Series([observation.get("fy")]), errors="coerce"
                ).iloc[0]
                qtrs = _duration_qtrs(start_date, end_date)
                if not _eligible_representation(
                    concept=concept,
                    form=obs_form,
                    fp=fp,
                    qtrs=qtrs,
                ):
                    continue
                eligible_rows += 1

                value = pd.to_numeric(
                    pd.Series([observation.get("val")]), errors="coerce"
                ).iloc[0]
                if pd.isna(value):
                    continue
                numeric_rows += 1

                rows.append({
                    "ticker": ticker,
                    "adsh": accession,
                    "tag": tag,
                    "version": "companyfacts-current-ttm",
                    "ddate": end_date.strftime("%Y%m%d"),
                    "qtrs": int(qtrs),
                    "uom": str(uom),
                    "segments": "",
                    "coreg": "",
                    "value": float(value),
                    "footnote": "",
                    "ddate_date": end_date.isoformat(),
                    "start_date": start_date.isoformat(),
                    "duration_days": (end_date - start_date).days + 1,
                    "concept": concept,
                    "source_tag": tag,
                    "taxonomy": "us-gaap",
                    "cik": int(cik),
                    "name": company_name,
                    "form": obs_form,
                    "fy": fy,
                    "fp": fp,
                    "period_date": report_date.isoformat(),
                    "filed_date": filed_date.isoformat(),
                    "accepted_at": accepted_at,
                    "source_zip": "",
                    "source_system": "sec_companyfacts_current_ttm",
                    "context_limitation": (
                        "companyfacts lacks quarterly DIM/PRE context; "
                        "V2 TTM candidate only"
                    ),
                })

    frame = pd.DataFrame(rows)
    if not frame.empty:
        dedup = [
            "adsh",
            "cik",
            "concept",
            "ddate_date",
            "qtrs",
            "uom",
            "value",
            "source_tag",
        ]
        frame = (
            frame.sort_values(
                ["concept", "qtrs", "source_tag", "uom", "value"],
                kind="stable",
            )
            .drop_duplicates(subset=dedup, keep="first")
            .reset_index(drop=True)
        )

    return frame, CurrentTtmDurationAudit(
        concepts_seen=concepts_seen,
        source_rows_seen=source_rows_seen,
        rows_matching_accession=accession_rows,
        rows_exact_report_date=exact_report_rows,
        rows_period_eligible=eligible_rows,
        rows_numeric_value=numeric_rows,
        rows_output=len(frame),
    )


def load_current_ttm_duration_candidates(
    *,
    discovery: pd.DataFrame,
    cache_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract all usable current-filing TTM duration candidates."""

    usable = discovery.loc[
        discovery["status"].astype(str).eq("new_filing_cached")
    ].copy()

    frames: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []

    for row in usable.itertuples(index=False):
        cik = int(row.cik)
        ticker = str(row.ticker).upper()
        accession = str(row.accession)
        path = cache_dir / "companyfacts" / f"CIK{cik:010d}.json"

        if not path.exists():
            audit_rows.append({
                "ticker": ticker,
                "cik": cik,
                "accession": accession,
                "status": "missing_companyfacts_cache",
                "rows_output": 0,
                "error": str(path),
            })
            continue

        try:
            payload = load_companyfacts(path)
            frame, audit = extract_current_ttm_duration_candidates(
                payload,
                ticker=ticker,
                accession=accession,
                cik=cik,
                company_name=str(
                    payload.get("entityName")
                    or getattr(row, "company_name", "")
                    or ticker
                ),
                form=str(row.form),
                report_date=pd.Timestamp(row.report_date).date(),
                filed_date=pd.Timestamp(row.filing_date).date(),
                accepted_at=str(row.accepted_at),
            )
            if not frame.empty:
                frames.append(frame)
            audit_rows.append({
                "ticker": ticker,
                "cik": cik,
                "accession": accession,
                "status": "ok" if not frame.empty else "no_candidates",
                **audit.__dict__,
                "error": "",
            })
        except Exception as exc:
            audit_rows.append({
                "ticker": ticker,
                "cik": cik,
                "accession": accession,
                "status": "error",
                "rows_output": 0,
                "error": str(exc),
            })

    candidates = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame()
    )
    audits = pd.DataFrame(audit_rows)
    return candidates, audits
