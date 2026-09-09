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
        "form": "10-Q",
        "segments": "EquityComponents=CommonStock;",
        "uom": "shares",
        "adsh": "000123-26-000001",
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
    assert result["classification"] == "safe_alternate_candidate"
    assert result["proposed_rule_candidate"] is True


def test_blank_and_exact_conflict_is_rejected():
    facts = pd.DataFrame([
        _fact(value=100.0, segments=""),
        _fact(value=110.0, segments="EquityComponents=CommonStock;"),
    ])
    result = classify_case(_row(), facts, pd.Timestamp("2026-09-01"))
    assert result["classification"] == "ambiguous_conflicting"
    assert result["proposed_rule_candidate"] is False
