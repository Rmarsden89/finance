from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from finance.data.sec_concepts import CANONICAL_TAGS


INSTANT_CONCEPTS = {
    "total_assets",
    "total_liabilities",
    "shareholders_equity",
    "cash",
    "shares_outstanding",
}
QUARTERLY_INCOME_CONCEPTS = {
    "revenue",
    "net_income",
    "operating_income",
}
QUARTERLY_YTD_CONCEPTS = {
    "operating_cash_flow",
    "capital_expenditures",
}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
FP_TO_YTD_QTRS = {"Q1": 1, "Q2": 2, "Q3": 3}


@dataclass(frozen=True)
class CurrentFactAudit:
    concepts_seen: int
    unit_series_seen: int
    source_rows_seen: int
    rows_matching_accession: int
    rows_current_period: int
    rows_period_eligible: int
    rows_output: int


def load_companyfacts(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tag_to_concept() -> dict[str, str]:
    result: dict[str, str] = {}
    for concept, tags in CANONICAL_TAGS.items():
        for tag in tags:
            result[tag] = concept
    return result


def _duration_qtrs(start: date | None, end: date) -> int | None:
    if start is None:
        return 0

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


def _qtrs_for(
    *,
    concept: str,
    form: str,
    fp: str,
    start: date | None,
    end: date,
) -> int | None:
    observed_qtrs = _duration_qtrs(start, end)

    if concept in INSTANT_CONCEPTS:
        return 0 if start is None else None

    if form in ANNUAL_FORMS:
        return 4 if observed_qtrs == 4 else None

    if form in QUARTERLY_FORMS and concept in QUARTERLY_INCOME_CONCEPTS:
        return 1 if observed_qtrs == 1 else None

    if form in QUARTERLY_FORMS and concept in QUARTERLY_YTD_CONCEPTS:
        expected = FP_TO_YTD_QTRS.get(fp.upper())
        return expected if observed_qtrs == expected else None

    return None


def extract_companyfacts_candidates(
    payload: dict,
    *,
    accession: str,
    cik: int,
    company_name: str,
    form: str,
    report_date: date,
    filed_date: date,
    accepted_at: str,
) -> tuple[pd.DataFrame, CurrentFactAudit]:
    """Extract current filing candidates from SEC companyfacts.

    This intentionally does not claim parity with the quarterly Financial
    Statement Data Sets. SEC companyfacts lacks the original DIM/PRE context
    used by the historical canonical pipeline. The result is therefore a
    reviewable candidate layer, not production winner facts.
    """

    tag_map = _tag_to_concept()
    us_gaap = ((payload.get("facts") or {}).get("us-gaap") or {})

    concepts_seen = 0
    unit_series_seen = 0
    source_rows_seen = 0
    accession_rows = 0
    current_period_rows = 0
    eligible_rows = 0
    rows: list[dict] = []

    for tag, fact_payload in us_gaap.items():
        concept = tag_map.get(tag)
        if concept is None:
            continue
        concepts_seen += 1

        units = fact_payload.get("units") or {}
        for uom, observations in units.items():
            unit_series_seen += 1
            for observation in observations or []:
                source_rows_seen += 1
                if str(observation.get("accn") or "") != accession:
                    continue
                accession_rows += 1

                raw_end = observation.get("end")
                if not raw_end:
                    continue
                try:
                    end_date = date.fromisoformat(str(raw_end)[:10])
                except ValueError:
                    continue

                raw_start = observation.get("start")
                start_date = None
                if raw_start:
                    try:
                        start_date = date.fromisoformat(str(raw_start)[:10])
                    except ValueError:
                        continue

                if end_date != report_date:
                    continue
                current_period_rows += 1

                obs_form = str(observation.get("form") or form)
                fp = str(observation.get("fp") or "").upper()
                fy = observation.get("fy")
                qtrs = _qtrs_for(
                    concept=concept,
                    form=obs_form,
                    fp=fp,
                    start=start_date,
                    end=end_date,
                )
                if qtrs is None:
                    continue
                eligible_rows += 1

                value = observation.get("val")
                if value is None:
                    continue

                rows.append(
                    {
                        "adsh": accession,
                        "tag": tag,
                        "version": "companyfacts-current",
                        "ddate": end_date.strftime("%Y%m%d"),
                        "qtrs": qtrs,
                        "uom": uom,
                        "segments": "",
                        "coreg": "",
                        "value": value,
                        "footnote": "",
                        "ddate_date": end_date.isoformat(),
                        "start_date": (
                            start_date.isoformat() if start_date else ""
                        ),
                        "duration_days": (
                            (end_date - start_date).days + 1
                            if start_date
                            else 0
                        ),
                        "concept": concept,
                        "source_tag": tag,
                        "cik": cik,
                        "name": company_name,
                        "form": obs_form,
                        "fy": fy,
                        "fp": fp,
                        "period_date": report_date.isoformat(),
                        "filed_date": filed_date.isoformat(),
                        "accepted_at": accepted_at,
                        "source_zip": "",
                        "source_system": "sec_companyfacts_current",
                        "context_limitation": (
                            "companyfacts lacks quarterly DIM/PRE context; "
                            "candidate only"
                        ),
                    }
                )

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = (
            frame.sort_values(
                ["concept", "source_tag", "uom", "value"],
                kind="stable",
            )
            .drop_duplicates(
                subset=[
                    "adsh",
                    "concept",
                    "source_tag",
                    "ddate_date",
                    "qtrs",
                    "uom",
                    "value",
                ],
                keep="first",
            )
            .reset_index(drop=True)
        )

    audit = CurrentFactAudit(
        concepts_seen=concepts_seen,
        unit_series_seen=unit_series_seen,
        source_rows_seen=source_rows_seen,
        rows_matching_accession=accession_rows,
        rows_current_period=current_period_rows,
        rows_period_eligible=eligible_rows,
        rows_output=len(frame),
    )
    return frame, audit
