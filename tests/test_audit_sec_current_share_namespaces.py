from __future__ import annotations

import json

import pandas as pd

from scripts.audit_sec_current_share_namespaces import (
    build_namespace_audit,
    inspect_share_namespaces,
    summarize_namespace_audit,
)


def _observation(value: float, *, accession: str = "000123-26-000001") -> dict:
    return {
        "accn": accession,
        "end": "2026-06-30",
        "filed": "2026-07-30",
        "form": "10-Q",
        "val": value,
    }


def _payload(*, us_gaap: list[dict] | None = None, dei: list[dict] | None = None) -> dict:
    facts: dict = {}
    if us_gaap is not None:
        facts["us-gaap"] = {
            "CommonStockSharesOutstanding": {"units": {"shares": us_gaap}}
        }
    if dei is not None:
        facts["dei"] = {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": dei}}
        }
    return {"facts": facts}


def test_inspect_classifies_agreement_conflict_and_single_namespace() -> None:
    expected = {
        "both_agree": _payload(us_gaap=[_observation(100)], dei=[_observation(100)]),
        "both_conflict": _payload(us_gaap=[_observation(100)], dei=[_observation(110)]),
        "us_gaap_only": _payload(us_gaap=[_observation(100)]),
        "dei_only": _payload(dei=[_observation(100)]),
        "neither": _payload(),
    }
    for classification, payload in expected.items():
        result = inspect_share_namespaces(
            payload,
            accession="000123-26-000001",
            report_date="2026-06-30",
        )
        assert result["namespace_classification"] == classification


def test_inspect_ignores_other_accessions_dates_units_and_nonpositive_values() -> None:
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            _observation(100, accession="000123-26-000000"),
                            {**_observation(100), "end": "2026-03-31"},
                            _observation(0),
                        ],
                        "USD": [_observation(100)],
                    }
                }
            }
        }
    }
    result = inspect_share_namespaces(
        payload,
        accession="000123-26-000001",
        report_date="2026-06-30",
    )
    assert result["namespace_classification"] == "neither"
    assert result["dei_observation_count"] == 0


def test_build_flags_only_dei_only_cases_with_missing_snapshot_shares(tmp_path) -> None:
    discovery = pd.DataFrame(
        [
            {
                "ticker": "MISS",
                "cik": 123,
                "accession": "000123-26-000001",
                "form": "10-Q",
                "report_date": "2026-06-30",
                "filing_date": "2026-07-30",
                "status": "new_filing_cached",
            },
            {
                "ticker": "HAVE",
                "cik": 124,
                "accession": "000124-26-000001",
                "form": "10-Q",
                "report_date": "2026-06-30",
                "filing_date": "2026-07-30",
                "status": "new_filing_cached",
            },
        ]
    )
    snapshot = pd.DataFrame(
        [
            {"ticker": "MISS", "cik": 123, "shares_outstanding": ""},
            {"ticker": "HAVE", "cik": 124, "shares_outstanding": 90},
        ]
    )
    for cik, accession in ((123, "000123-26-000001"), (124, "000124-26-000001")):
        payload = _payload(dei=[_observation(100, accession=accession)])
        (tmp_path / f"CIK{cik:010d}.json").write_text(json.dumps(payload))

    audit = build_namespace_audit(
        discovery,
        snapshot,
        cache_dir=tmp_path,
        as_of=pd.Timestamp("2026-09-15"),
    )
    flags = dict(zip(audit["ticker"], audit["potential_missing_coverage_recovery"]))
    assert flags == {"MISS": True, "HAVE": False}

    summary = summarize_namespace_audit(audit)
    assert summary["filings"].sum() == 2
    assert summary["potential_missing_coverage_recoveries"].sum() == 1


def test_build_reports_missing_cache_without_failing(tmp_path) -> None:
    discovery = pd.DataFrame(
        [
            {
                "ticker": "MISS",
                "cik": 123,
                "accession": "000123-26-000001",
                "report_date": "2026-06-30",
                "filing_date": "2026-07-30",
            }
        ]
    )
    audit = build_namespace_audit(
        discovery,
        pd.DataFrame(),
        cache_dir=tmp_path,
        as_of=pd.Timestamp("2026-09-15"),
    )
    assert audit.iloc[0]["namespace_classification"] == "cache_missing"
    assert audit.iloc[0]["potential_missing_coverage_recovery"] == False
