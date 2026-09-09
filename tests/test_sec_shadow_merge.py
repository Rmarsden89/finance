import pandas as pd

from finance.data.sec_shadow_merge import merge_current_sec_shadow


def row(**overrides):
    base = {
        "cik": 1,
        "adsh": "A",
        "concept": "revenue",
        "ddate_date": "2026-06-30",
        "qtrs": 1,
        "uom": "USD",
        "accepted_at": "2026-07-20T10:00:00",
        "value": 100,
        "source_tag": "Revenues",
    }
    base.update(overrides)
    return base


def test_adds_novel_current_fact_without_touching_history() -> None:
    historical = pd.DataFrame([row(adsh="OLD", value=90)])
    current = pd.DataFrame([row(adsh="NEW", value=100)])

    merged, audit, summary = merge_current_sec_shadow(historical, current)

    assert len(merged) == 2
    assert summary.current_rows_added == 1
    assert set(merged["source_system"]) == {
        "sec_quarterly_zip",
        "sec_companyfacts_current",
    }
    assert "added_current_winner" in set(audit["status"])


def test_existing_archived_fact_group_wins() -> None:
    historical = pd.DataFrame([row(value=100)])
    current = pd.DataFrame([row(value=101)])

    merged, audit, summary = merge_current_sec_shadow(historical, current)

    assert len(merged) == 1
    assert summary.current_rows_added == 0
    assert summary.rejected_existing_fact_group == 1
    assert audit.iloc[0]["status"] == "rejected_existing_fact_group"


def test_rejects_competing_current_values() -> None:
    historical = pd.DataFrame([row(adsh="OLD", value=90)])
    current = pd.DataFrame(
        [
            row(adsh="NEW", value=100, source_tag="Revenues"),
            row(adsh="NEW", value=101, source_tag="SalesRevenueNet"),
        ]
    )

    merged, _, summary = merge_current_sec_shadow(historical, current)

    assert len(merged) == 1
    assert summary.current_rows_added == 0
    assert summary.rejected_ambiguous_value_group == 2


def test_rejects_missing_acceptance() -> None:
    historical = pd.DataFrame([row(adsh="OLD", value=90)])
    current = pd.DataFrame([row(adsh="NEW", accepted_at=None)])

    merged, _, summary = merge_current_sec_shadow(historical, current)

    assert len(merged) == 1
    assert summary.rejected_invalid_acceptance == 1
