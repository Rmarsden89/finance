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
    bounded_share_candidates_seen: int
    bounded_share_candidates_selected: int
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
    allow_dei_share_fallback: bool = False,
    allow_bounded_dei_cover_date: bool = False,
) -> tuple[pd.DataFrame, CurrentFactAudit]:
    """Extract current filing candidates from SEC companyfacts.

    This intentionally does not claim parity with the quarterly Financial
    Statement Data Sets. SEC companyfacts lacks the original DIM/PRE context
    used by the historical canonical pipeline. The result is therefore a
    reviewable candidate layer, not production winner facts.
    """

    if allow_bounded_dei_cover_date and not allow_dei_share_fallback:
        raise ValueError(
            "bounded DEI cover-date fallback requires DEI share fallback"
        )

    tag_map = _tag_to_concept()
    taxonomies = payload.get("facts") or {}

    concepts_seen = 0
    unit_series_seen = 0
    source_rows_seen = 0
    accession_rows = 0
    current_period_rows = 0
    eligible_rows = 0
    bounded_share_candidates_seen = 0
    bounded_share_candidates_selected = 0
    rows: list[dict] = []

    try:
        accepted_date = date.fromisoformat(str(accepted_at)[:10])
    except (TypeError, ValueError):
        accepted_date = None

    def collect_taxonomy(
        taxonomy: str,
        *,
        shares_only: bool,
        namespace_selection_reason: str,
        date_selection_reason: str = "exact_report_date",
        target_rows: list[dict] | None = None,
        count_source_rows: bool = True,
    ) -> None:
        nonlocal concepts_seen
        nonlocal unit_series_seen
        nonlocal source_rows_seen
        nonlocal accession_rows
        nonlocal current_period_rows
        nonlocal eligible_rows

        taxonomy_facts = taxonomies.get(taxonomy) or {}
        for tag, fact_payload in taxonomy_facts.items():
            concept = tag_map.get(tag)
            if concept is None or (
                shares_only and concept != "shares_outstanding"
            ):
                continue
            if count_source_rows:
                concepts_seen += 1

            units = fact_payload.get("units") or {}
            for uom, observations in units.items():
                if count_source_rows:
                    unit_series_seen += 1
                for observation in observations or []:
                    if count_source_rows:
                        source_rows_seen += 1
                    if str(observation.get("accn") or "") != accession:
                        continue
                    if count_source_rows:
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

                    if date_selection_reason == "exact_report_date":
                        if end_date != report_date:
                            continue
                    elif date_selection_reason == "bounded_cover_date":
                        if (
                            concept != "shares_outstanding"
                            or accepted_date is None
                            or not report_date < end_date <= accepted_date
                        ):
                            continue
                    else:
                        raise ValueError(
                            f"Unsupported date selection: {date_selection_reason}"
                        )
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
                    if taxonomy != "dei":
                        eligible_rows += 1

                    value = observation.get("val")
                    if value is None:
                        continue
                    if taxonomy == "dei":
                        numeric_value = pd.to_numeric(
                            pd.Series([value]), errors="coerce"
                        ).iloc[0]
                        if (
                            str(uom).strip().lower() != "shares"
                            or pd.isna(numeric_value)
                            or numeric_value <= 0
                        ):
                            continue
                        eligible_rows += 1

                    output_rows = target_rows if target_rows is not None else rows
                    output_rows.append(
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
                            "taxonomy": taxonomy,
                            "namespace_selection_reason": (
                                namespace_selection_reason
                            ),
                            "date_selection_reason": date_selection_reason,
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
                                "DEI cover-page shares measured after report date; "
                                "candidate only"
                                if date_selection_reason == "bounded_cover_date"
                                else "companyfacts lacks quarterly DIM/PRE context; "
                                "candidate only"
                            ),
                        }
                    )

    collect_taxonomy(
        "us-gaap",
        shares_only=False,
        namespace_selection_reason="primary_us_gaap",
    )
    def usable_share_candidate(row: dict) -> bool:
        if (
            row["concept"] != "shares_outstanding"
            or str(row["uom"]).strip().lower() != "shares"
        ):
            return False
        value = pd.to_numeric(pd.Series([row["value"]]), errors="coerce").iloc[0]
        return bool(pd.notna(value) and value > 0)

    has_usable_us_gaap_shares = any(usable_share_candidate(row) for row in rows)
    if allow_dei_share_fallback and not has_usable_us_gaap_shares:
        rows = [
            row for row in rows if row["concept"] != "shares_outstanding"
        ]
        collect_taxonomy(
            "dei",
            shares_only=True,
            namespace_selection_reason="fallback_missing_us_gaap_shares",
        )

    if (
        allow_bounded_dei_cover_date
        and not any(usable_share_candidate(row) for row in rows)
    ):
        bounded_rows: list[dict] = []
        collect_taxonomy(
            "dei",
            shares_only=True,
            namespace_selection_reason="fallback_dei_cover_date",
            date_selection_reason="bounded_cover_date",
            target_rows=bounded_rows,
            count_source_rows=False,
        )
        bounded_share_candidates_seen = len(bounded_rows)
        if len(bounded_rows) == 1:
            rows.extend(bounded_rows)
            bounded_share_candidates_selected = 1

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
        bounded_share_candidates_seen=bounded_share_candidates_seen,
        bounded_share_candidates_selected=bounded_share_candidates_selected,
        rows_output=len(frame),
    )
    return frame, audit
