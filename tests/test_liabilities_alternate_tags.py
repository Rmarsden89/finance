from datetime import date

import pandas as pd

from finance.research.liabilities_alternate_tags import (
    classify_alternate_liabilities_evidence,
)


def _facts(rows):
    return pd.DataFrame(rows)


def test_alternate_liabilities_prefers_raw_direct():
    facts = _facts(
        [
            {
                "fact_name": "us-gaap:Liabilities",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "1000",
            },
            {
                "fact_name": "us-gaap:LiabilitiesCurrent",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "400",
            },
            {
                "fact_name": "us-gaap:LiabilitiesNoncurrent",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "600",
            },
        ]
    )

    evidence = classify_alternate_liabilities_evidence(
        facts,
        ticker="AAA",
        as_of=date(2026, 9, 15),
    )

    assert evidence.status == "raw_direct_liabilities_candidate"
    assert evidence.direct_value == 1000
    assert evidence.current_plus_noncurrent == 1000


def test_alternate_liabilities_flags_current_plus_noncurrent_only():
    facts = _facts(
        [
            {
                "fact_name": "us-gaap:LiabilitiesCurrent",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "400",
            },
            {
                "fact_name": "us-gaap:LiabilitiesNoncurrent",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "600",
            },
        ]
    )

    evidence = classify_alternate_liabilities_evidence(
        facts,
        ticker="AAA",
        as_of=date(2026, 9, 15),
    )

    assert evidence.status == "current_plus_noncurrent_candidate"
    assert evidence.current_plus_noncurrent == 1000


def test_alternate_liabilities_does_not_treat_total_like_as_liabilities():
    facts = _facts(
        [
            {
                "fact_name": "us-gaap:LiabilitiesAndStockholdersEquity",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "1500",
            }
        ]
    )

    evidence = classify_alternate_liabilities_evidence(
        facts,
        ticker="AAA",
        as_of=date(2026, 9, 15),
    )

    assert evidence.status == "total_like_only"
    assert evidence.direct_value is None
    assert "LiabilitiesAndStockholdersEquity" in evidence.total_like_tags


def test_alternate_liabilities_rejects_dimensioned_direct_fact():
    facts = _facts(
        [
            {
                "fact_name": "us-gaap:Liabilities",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-06-30",
                "context_dimensions": "us-gaap:VariableInterestEntityPrimaryBeneficiaryMember",
                "unit_ref": "USD",
                "scale": "0",
                "sign": "",
                "value_text": "1000",
            }
        ]
    )

    evidence = classify_alternate_liabilities_evidence(
        facts,
        ticker="AAA",
        as_of=date(2026, 9, 15),
    )

    assert evidence.status == "no_eligible_undimensioned_liability_evidence"
    assert evidence.direct_value is None
