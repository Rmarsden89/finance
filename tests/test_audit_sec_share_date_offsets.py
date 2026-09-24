from __future__ import annotations

import json

import pandas as pd

from scripts.audit_sec_share_date_offsets import (
    build_date_offset_audit,
    classify_company,
    inspect_date_offset_observations,
    summarize_date_offset_audit,
)


ACCESSION = "0000000123-26-000001"


def _observation(
    value: float,
    *,
    end: str,
    accession: str = ACCESSION,
    start: str | None = None,
) -> dict:
    row = {"accn": accession, "end": end, "val": value, "form": "10-Q"}
    if start is not None:
        row["start"] = start
    return row


def _payload(observations: list[dict], *, uom: str = "shares", taxonomy: str = "dei") -> dict:
    return {
        "facts": {
            taxonomy: {
                "EntityCommonStockSharesOutstanding": {
                    "units": {uom: observations}
                }
            }
        }
    }


def test_inspect_classifies_exact_bounded_before_and_after_acceptance() -> None:
    payload = _payload(
        [
            _observation(100, end="2026-06-29"),
            _observation(101, end="2026-06-30"),
            _observation(102, end="2026-07-03"),
            _observation(103, end="2026-08-01"),
        ]
    )
    rows = inspect_date_offset_observations(
        payload,
        accession=ACCESSION,
        report_date="2026-06-30",
        accepted_at="2026-07-30T16:30:00Z",
        filing_date="2026-07-30",
    )
    assert [row["observation_classification"] for row in rows] == [
        "before_report",
        "exact_report_date",
        "after_report_before_or_on_acceptance",
        "after_acceptance",
    ]
    assert [row["bounded_candidate"] for row in rows] == [False, False, True, False]


def test_inspect_ignores_duration_wrong_uom_nonpositive_and_wrong_accession() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            _observation(100, end="2026-07-03", start="2026-01-01"),
                            _observation(0, end="2026-07-03"),
                            _observation(100, end="2026-07-03", accession="other"),
                        ],
                        "USD": [_observation(100, end="2026-07-03")],
                    }
                }
            }
        }
    }
    assert inspect_date_offset_observations(
        payload,
        accession=ACCESSION,
        report_date="2026-06-30",
        accepted_at="2026-07-30",
        filing_date="2026-07-30",
    ) == []


def test_company_classification_detects_single_same_value_and_conflict() -> None:
    bounded = {
        "observation_classification": "after_report_before_or_on_acceptance",
        "bounded_candidate": True,
        "value": 100.0,
    }
    assert classify_company([bounded]) == "single_bounded_cover_candidate"
    assert classify_company([bounded, dict(bounded)]) == "multiple_same_value_bounded_candidates"
    assert classify_company([bounded, {**bounded, "value": 101.0}]) == "conflicting_bounded_candidates"
    assert classify_company(
        [{**bounded, "observation_classification": "exact_report_date", "bounded_candidate": False}]
    ) == "exact_candidate_present"


def test_build_starts_from_missing_snapshot_and_matches_discovery_by_cik(tmp_path) -> None:
    discovery = pd.DataFrame(
        [
            {
                "ticker": "OLD",
                "cik": 123,
                "accession": ACCESSION,
                "form": "10-Q",
                "report_date": "2026-06-30",
                "filing_date": "2026-07-30",
                "accepted_at": "2026-07-30T16:00:00Z",
                "status": "new_filing_cached",
            }
        ]
    )
    snapshot = pd.DataFrame(
        [
            {"ticker": "NEW", "cik": 123, "shares_outstanding": ""},
            {"ticker": "HAVE", "cik": 124, "shares_outstanding": 50},
            {"ticker": "NODISC", "cik": 125, "shares_outstanding": ""},
        ]
    )
    payload = _payload([_observation(100, end="2026-07-03")])
    (tmp_path / "CIK0000000123.json").write_text(json.dumps(payload))

    audit = build_date_offset_audit(
        discovery,
        snapshot,
        cache_dir=tmp_path,
        as_of=pd.Timestamp("2026-09-15"),
    )
    assert set(audit["ticker"]) == {"NEW", "NODISC"}
    new = audit.loc[audit["ticker"].eq("NEW")].iloc[0]
    assert new["company_classification"] == "single_bounded_cover_candidate"
    assert bool(new["bounded_candidate"])
    assert audit.loc[audit["ticker"].eq("NODISC"), "company_classification"].iloc[0] == "no_current_discovery"

    summary = summarize_date_offset_audit(audit)
    assert summary["companies"].sum() == 2
    assert summary["bounded_candidates"].sum() == 1
