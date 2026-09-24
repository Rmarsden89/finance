from datetime import date

import pandas as pd

from finance.research.raw_share_validation import (
    build_raw_share_candidate,
    raw_share_validation_row,
)


def _facts(rows):
    return pd.DataFrame(rows)


def test_raw_share_candidate_prefers_unique_undimensioned_value():
    facts = _facts(
        [
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-07-31",
                "context_dimensions": "",
                "unit_ref": "shares",
                "scale": "0",
                "sign": "",
                "value_text": "722,340,711",
            },
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-07-31",
                "context_dimensions": "dow:TheDowChemicalCompanyMember",
                "unit_ref": "shares",
                "scale": "0",
                "sign": "",
                "value_text": "100",
            },
        ]
    )

    candidate = build_raw_share_candidate(
        facts,
        ticker="DOW",
        as_of=date(2026, 9, 15),
    )

    assert candidate.status == "candidate"
    assert candidate.selection_rule == "undimensioned_preferred"
    assert candidate.value == 722_340_711


def test_raw_share_candidate_sums_valid_share_classes():
    facts = _facts(
        [
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-07-31",
                "context_dimensions": "us-gaap:CommonClassAMember",
                "unit_ref": "shares",
                "scale": "0",
                "sign": "",
                "value_text": "360,503,747",
            },
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-07-31",
                "context_dimensions": "us-gaap:CommonClassBMember",
                "unit_ref": "shares",
                "scale": "0",
                "sign": "",
                "value_text": "180,548,891",
            },
        ]
    )

    candidate = build_raw_share_candidate(
        facts,
        ticker="NWS",
        as_of=date(2026, 9, 15),
    )

    assert candidate.status == "candidate"
    assert candidate.selection_rule == "share_class_sum"
    assert candidate.value == 541_052_638
    assert candidate.component_count == 2


def test_raw_share_candidate_rejects_non_share_class_dimension():
    facts = _facts(
        [
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-07-31",
                "context_dimensions": "dow:TheDowChemicalCompanyMember",
                "unit_ref": "shares",
                "scale": "0",
                "sign": "",
                "value_text": "100",
            }
        ]
    )

    candidate = build_raw_share_candidate(
        facts,
        ticker="DOW",
        as_of=date(2026, 9, 15),
    )

    assert candidate.status == "unsupported_dimension"
    assert candidate.value is None


def test_raw_share_candidate_applies_ix_scale():
    facts = _facts(
        [
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-08-01T12:00:00Z",
                "context_instant": "2026-07-21",
                "context_dimensions": "",
                "unit_ref": "shares",
                "scale": "6",
                "sign": "",
                "value_text": "164.6",
            }
        ]
    )

    candidate = build_raw_share_candidate(
        facts,
        ticker="IQV",
        as_of=date(2026, 9, 15),
    )

    assert candidate.status == "candidate"
    assert candidate.value == 164_600_000


def test_raw_share_candidate_fails_closed_after_decision_cutoff():
    facts = _facts(
        [
            {
                "fact_name": "dei:EntityCommonStockSharesOutstanding",
                "accepted_at": "2026-09-15T12:00:00Z",
                "context_instant": "2026-09-14",
                "context_dimensions": "",
                "unit_ref": "shares",
                "scale": "0",
                "sign": "",
                "value_text": "100",
            }
        ]
    )

    candidate = build_raw_share_candidate(
        facts,
        ticker="AAA",
        as_of=date(2026, 9, 15),
    )

    assert candidate.status == "not_pit_eligible"
    assert candidate.value is None


def test_raw_share_validation_bands():
    candidate = build_raw_share_candidate(
        _facts(
            [
                {
                    "fact_name": "dei:EntityCommonStockSharesOutstanding",
                    "accepted_at": "2026-08-01T12:00:00Z",
                    "context_instant": "2026-07-31",
                    "context_dimensions": "",
                    "unit_ref": "shares",
                    "scale": "0",
                    "sign": "",
                    "value_text": "1000",
                }
            ]
        ),
        ticker="AAA",
        as_of=date(2026, 9, 15),
    )

    exact = raw_share_validation_row(candidate=candidate, canonical_value=1000)
    assert exact["validation_band"] == "exact_match"

    close = raw_share_validation_row(candidate=candidate, canonical_value=1005)
    assert close["validation_band"] == "within_1_pct"

    far = raw_share_validation_row(candidate=candidate, canonical_value=1200)
    assert far["validation_band"] == "material_difference"
