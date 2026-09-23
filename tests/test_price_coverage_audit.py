from __future__ import annotations

from datetime import date

import pandas as pd

from finance.data.models import MembershipInterval
from finance.research.price_coverage_audit import (
    classify_unresolved_price_row,
    identity_summary_by_ticker,
    membership_days_by_ticker_and_year,
    yearly_panel_coverage,
)


def test_membership_days_are_split_by_year() -> None:
    intervals = [
        MembershipInterval(
            index_name="sp500",
            ticker="AAA",
            start_date=date(2019, 12, 30),
            end_date=date(2020, 1, 3),
            cik=None,
            company_name="A",
            source="test",
        )
    ]
    total, yearly = membership_days_by_ticker_and_year(
        intervals,
        start=date(2019, 1, 1),
        end=date(2020, 12, 31),
    )

    assert total["AAA"] == 4
    assert yearly["AAA"][2019] == 2
    assert yearly["AAA"][2020] == 2


def test_identity_summary_distinguishes_resolved_partial_unresolved() -> None:
    panel = pd.DataFrame([
        {"ticker": "AAA", "identity_resolved": True},
        {"ticker": "AAA", "identity_resolved": True},
        {"ticker": "BBB", "identity_resolved": True},
        {"ticker": "BBB", "identity_resolved": False},
        {"ticker": "CCC", "identity_resolved": False},
    ])

    result = identity_summary_by_ticker(panel).set_index("ticker")

    assert result.loc["AAA", "identity_status"] == "identity_resolved"
    assert result.loc["BBB", "identity_status"] == "identity_partial"
    assert result.loc["CCC", "identity_status"] == "identity_unresolved"


def test_unresolved_classification_keeps_identity_and_provider_dimensions() -> None:
    row = pd.Series({
        "identity_status": "identity_partial",
        "selected_status": "partial_boundary_coverage",
        "tiingo_status": "missing",
        "stooq_status": "partial_boundary_coverage",
        "stooq_exclusion_reason": "",
    })

    assert (
        classify_unresolved_price_row(row)
        == "identity_partial|provider_partial_coverage"
    )


def test_quality_exclusion_is_distinct_from_missing_provider() -> None:
    row = pd.Series({
        "identity_status": "identity_resolved",
        "selected_status": "missing",
        "tiingo_status": "missing",
        "stooq_status": "quality_excluded",
        "stooq_exclusion_reason": "known bad adjusted return series",
    })

    assert (
        classify_unresolved_price_row(row)
        == "identity_resolved|provider_quality_excluded"
    )


def test_yearly_panel_coverage_reports_before_state() -> None:
    panel = pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "identity_resolved": True,
            "price_available": True,
            "research_ready": True,
        },
        {
            "decision_date": "2020-01-03",
            "identity_resolved": True,
            "price_available": False,
            "research_ready": False,
        },
        {
            "decision_date": "2021-01-08",
            "identity_resolved": False,
            "price_available": False,
            "research_ready": False,
        },
    ])

    result = yearly_panel_coverage(panel)

    y2020 = result.loc[result["year"].astype(str).eq("2020")].iloc[0]
    assert y2020["price_available_rows"] == 1
    assert y2020["price_available_pct"] == 0.5
    all_row = result.loc[result["year"].astype(str).eq("ALL")].iloc[0]
    assert all_row["panel_rows"] == 3
