from __future__ import annotations

import pandas as pd

from scripts.audit_missing_current_shares import classify_case


def _row() -> pd.Series:
    return pd.Series(
        {
            "ticker": "TEST",
            "company_name": "Test Co",
            "cik_int": 123,
            "shares_outstanding": "",
        }
    )


def _fact(**overrides) -> dict:
    result = {
        "value": 100.0,
        "accepted_at": "2026-08-01 12:00:00",
        "ddate_date": "2026-06-30",
        "period_date": "2026-06-30",
        "qtrs": 0,
        "form": "10-Q",
        "segments": "EquityComponents=CommonStock;",
        "coreg": "",
        "uom": "shares",
        "adsh": "000123-26-000001",
        "has_equity_statement": True,
        "presentation_statements": "EQ",
    }
    result.update(overrides)
    return result


def test_no_evidence_is_not_a_candidate():
    result = classify_case(_row(), pd.DataFrame(), pd.Timestamp("2026-09-01"))
    assert result["classification"] == "no_usable_sec_evidence"
    assert result["proposed_rule_candidate"] is False


def test_exact_common_stock_only_is_shadow_candidate():
    facts = pd.DataFrame([_fact()])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "exact_dimension_candidate_pending_validation"
    assert result["proposed_rule_candidate"] is True
    assert result["fact_age_days"] == 63


def test_other_context_elsewhere_in_selected_filing_is_rejected():
    facts = pd.DataFrame([
        _fact(),
        _fact(
            value=90.0,
            ddate_date="2025-12-31",
            segments="ShareClass=ClassA;",
        ),
    ])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "ambiguous_conflicting"
    assert result["proposed_rule_candidate"] is False
    assert result["selected_filing_other_dimensional_rows"] == 1


def test_candidate_requires_equity_statement_presentation():
    facts = pd.DataFrame([_fact(has_equity_statement=False)])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "other"
    assert result["proposed_rule_candidate"] is False


def test_clean_non_dimensional_statement_mismatch_is_v2_candidate():
    facts = pd.DataFrame([
        _fact(
            segments="",
            has_equity_statement=False,
            presentation_statements="IS",
            value=296_000_000,
        ),
        _fact(
            segments="",
            has_equity_statement=False,
            presentation_statements="IS",
            value=306_700_000,
            ddate_date="2025-06-30",
        ),
    ])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "non_dimensional_statement_mismatch_candidate"
    assert result["proposed_rule_candidate"] is True
    assert result["clean_except_statement_placement"] is True
    assert result["selected_filing_presentations"] == "IS"
    assert result["selected_filing_canonical_statement_matched"] is False


def test_canonical_statement_is_not_a_mismatch_candidate():
    facts = pd.DataFrame([
        _fact(
            segments="",
            has_equity_statement=False,
            presentation_statements="CP",
        )
    ])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "other"
    assert result["proposed_rule_candidate"] is False
    assert result["clean_except_statement_placement"] is False
    assert result["selected_filing_canonical_statement_matched"] is True


def test_blank_and_exact_conflict_is_rejected():
    facts = pd.DataFrame([
        _fact(value=100.0, segments=""),
        _fact(value=110.0, segments="EquityComponents=CommonStock;"),
    ])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "ambiguous_conflicting"
    assert result["proposed_rule_candidate"] is False


def test_unmapped_identity_is_explicit():
    row = _row()
    row["cik_int"] = pd.NA
    result = classify_case(row, pd.DataFrame(), pd.Timestamp("2026-09-01"))
    assert result["classification"] == "identity_history_issue"
    assert result["proposed_rule_candidate"] is False
