import json
from pathlib import Path

import pandas as pd

from finance.research.liabilities_audit import (
    classify_liabilities_gaps,
    liabilities_coverage_by_year,
)


def test_liabilities_audit_separates_defects_research_and_true_gaps(
    tmp_path: Path,
) -> None:
    snapshot = pd.DataFrame(
        [
            {"ticker": "GOOD", "cik": 1, "total_liabilities": 100},
            {"ticker": "BUG", "cik": 2, "total_liabilities": None},
            {"ticker": "LATE", "cik": 3, "total_liabilities": None},
            {"ticker": "IDENT", "cik": 4, "total_liabilities": None},
            {"ticker": "ALT", "cik": 5, "total_liabilities": None},
            {"ticker": "NOFACT", "cik": 6, "total_liabilities": None},
            {"ticker": "NODISC", "cik": 7, "total_liabilities": None},
            {"ticker": "ZERO", "cik": 8, "total_liabilities": 0},
        ]
    )
    discovery = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "status": "new_filing_cached",
                "accession": f"acc-{ticker}",
                "report_date": "2026-06-30",
                "accepted_at": "2026-09-14T12:00:00Z",
            }
            for ticker in ("BUG", "LATE", "IDENT", "ALT", "NOFACT", "ZERO")
        ]
    )
    candidates = pd.DataFrame(
        [
            {
                "ticker": "BUG",
                "concept": "total_liabilities",
                "value": 20,
                "accepted_at": "2026-09-14T12:00:00Z",
                "adsh": "acc-BUG",
                "ddate_date": "2026-06-30",
                "uom": "USD",
                "source_tag": "Liabilities",
            },
            {
                "ticker": "LATE",
                "concept": "total_liabilities",
                "value": 30,
                "accepted_at": "2026-09-18T12:00:00Z",
                "adsh": "acc-LATE",
                "ddate_date": "2026-06-30",
                "uom": "USD",
                "source_tag": "Liabilities",
            },
            {
                "ticker": "IDENT",
                "concept": "total_assets",
                "value": 100,
                "accepted_at": "2026-09-14T12:00:00Z",
                "adsh": "acc-IDENT",
                "ddate_date": "2026-06-30",
                "uom": "USD",
                "source_tag": "Assets",
            },
            {
                "ticker": "IDENT",
                "concept": "shareholders_equity",
                "value": 40,
                "accepted_at": "2026-09-14T12:00:00Z",
                "adsh": "acc-IDENT",
                "ddate_date": "2026-06-30",
                "uom": "USD",
                "source_tag": "StockholdersEquity",
            },
        ]
    )
    companyfacts = tmp_path / "companyfacts"
    companyfacts.mkdir()
    for cik in (2, 3, 4, 5, 6, 8):
        payload = {"facts": {"us-gaap": {}}}
        if cik == 5:
            payload["facts"]["us-gaap"]["LiabilitiesCurrent"] = {
                "units": {
                    "USD": [
                        {"accn": "acc-ALT", "end": "2026-06-30", "val": 25}
                    ]
                }
            }
        (companyfacts / f"CIK{cik:010d}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    scored = pd.DataFrame(
        {
            "ticker": snapshot["ticker"],
            "decision_date": "2026-09-15",
            "financial_health_factor_count": [3, 1, 1, 1, 1, 1, 1, 1],
            "financial_health_eligible": [True, False, False, False, False, False, False, False],
            "financial_health_score": [50.0, None, None, None, None, None, None, None],
            "top_conviction_eligible": [True, False, False, False, False, False, False, False],
        }
    )

    detail, summary = classify_liabilities_gaps(
        snapshot=snapshot,
        discovery=discovery,
        candidate_audit=pd.DataFrame(
            [{"ticker": ticker, "status": "ok"} for ticker in discovery["ticker"]]
        ),
        candidates=candidates,
        merge_audit=pd.DataFrame(columns=["ticker", "concept", "status"]),
        scored=scored,
        cache_dir=tmp_path,
        as_of=pd.Timestamp("2026-09-15"),
    )
    lookup = detail.set_index("ticker")

    assert lookup.loc["BUG", "recommended_action"] == "candidate_not_selected"
    assert lookup.loc["LATE", "classification"] == "liabilities_candidate_not_pit_eligible"
    assert lookup.loc["IDENT", "recommended_action"] == "research_identity_candidate"
    assert lookup.loc["IDENT", "identity_derived_liabilities_values"] == "60"
    assert lookup.loc["ALT", "recommended_action"] == "research_alternate_tag"
    assert lookup.loc["NOFACT", "recommended_action"] == "documented_no_supported_fact"
    assert lookup.loc["NODISC", "recommended_action"] == "targeted_sec_refresh"
    assert lookup.loc["ZERO", "recommended_action"] == "investigate_invalid_value"
    assert summary.positive_liabilities == 1
    assert summary.residual_rows == 7
    assert summary.health_eligible == 1
    assert summary.candidate_not_selected == 1
    assert summary.research_identity_candidate == 1
    assert summary.research_alternate_tag == 1


def test_identity_candidate_requires_same_accession_period_and_units() -> None:
    snapshot = pd.DataFrame(
        [{"ticker": "MISMATCH", "cik": 1, "total_liabilities": None}]
    )
    candidates = pd.DataFrame(
        [
            {
                "ticker": "MISMATCH",
                "concept": "total_assets",
                "value": 100,
                "accepted_at": "2026-09-14T12:00:00Z",
                "adsh": "filing-a",
                "ddate_date": "2026-06-30",
                "uom": "USD",
                "source_tag": "Assets",
            },
            {
                "ticker": "MISMATCH",
                "concept": "shareholders_equity",
                "value": 40,
                "accepted_at": "2026-09-14T12:00:00Z",
                "adsh": "filing-b",
                "ddate_date": "2026-06-30",
                "uom": "USD",
                "source_tag": "StockholdersEquity",
            },
        ]
    )

    detail, _ = classify_liabilities_gaps(
        snapshot=snapshot,
        discovery=pd.DataFrame(
            [{"ticker": "MISMATCH", "status": "new_filing_cached", "accession": "filing-a"}]
        ),
        candidate_audit=pd.DataFrame(),
        candidates=candidates,
        merge_audit=pd.DataFrame(),
        scored=pd.DataFrame(),
        cache_dir=Path("/missing"),
        as_of=pd.Timestamp("2026-09-15"),
    )

    assert not bool(detail.loc[0, "same_context_identity_candidate_present"])
    assert detail.loc[0, "recommended_action"] == "targeted_sec_refresh"


def test_liabilities_coverage_by_year_uses_positive_values_and_health_gate() -> None:
    scored = pd.DataFrame(
        {
            "ticker": ["A", "B", "A"],
            "decision_date": ["2025-01-03", "2025-01-03", "2026-01-02"],
            "total_liabilities": [10, None, 20],
            "financial_health_eligible": [True, False, True],
        }
    )

    result = liabilities_coverage_by_year(scored).set_index("year")

    assert result.loc[2025, "rows"] == 2
    assert result.loc[2025, "positive_liabilities"] == 1
    assert result.loc[2025, "financial_health_eligible"] == 1
    assert result.loc[2026, "liabilities_coverage_pct"] == 1.0


def test_liabilities_audit_has_no_broker_or_order_imports() -> None:
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "audit_v2_liabilities_gaps.py"
    ).read_text(encoding="utf-8")
    prohibited = ("finance.broker", "finance.shadow", "place_equity_order")
    assert not any(value in source for value in prohibited)
