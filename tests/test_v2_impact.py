from pathlib import Path

import pandas as pd

from finance.research.v2_impact import compare_v1_v2_impact


def _snapshot(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "cik": 1,
        "company_name": "Example",
        "shares_outstanding": None,
        "shares_outstanding_period_date": "",
        "shares_outstanding_filing_period_date": "",
        "shares_outstanding_filed_date": "",
        "shares_outstanding_accepted_at": "",
        "shares_outstanding_form": "",
        "shares_outstanding_source_tag": "",
        "shares_outstanding_adsh": "",
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def _scored(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "decision_date": "2026-09-15",
        "valuation_score": None,
        "top_conviction_eligible": False,
        "long_growth_v1_score": None,
        "market_cap": None,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_comparison_reports_coverage_eligibility_and_top10_changes() -> None:
    baseline_snapshot = _snapshot([
        {"ticker": "AAA", "shares_outstanding": 100},
        {"ticker": "BBB", "cik": 2, "company_name": "Beta"},
    ])
    challenger_snapshot = _snapshot([
        {"ticker": "AAA", "shares_outstanding": 100},
        {
            "ticker": "BBB",
            "cik": 2,
            "company_name": "Beta",
            "shares_outstanding": 200,
            "shares_outstanding_period_date": "2026-07-20",
            "shares_outstanding_filing_period_date": "2026-06-30",
            "shares_outstanding_filed_date": "2026-08-01",
            "shares_outstanding_accepted_at": "2026-08-01T16:00:00",
            "shares_outstanding_form": "10-Q",
            "shares_outstanding_source_tag": "EntityCommonStockSharesOutstanding",
            "shares_outstanding_adsh": "B",
        },
    ])
    baseline_scored = _scored([
        {
            "ticker": "AAA",
            "valuation_score": 60,
            "top_conviction_eligible": True,
            "long_growth_v1_score": 80,
        },
        {"ticker": "BBB", "long_growth_v1_score": 70},
    ])
    challenger_scored = _scored([
        {
            "ticker": "AAA",
            "valuation_score": 60,
            "top_conviction_eligible": True,
            "long_growth_v1_score": 80,
        },
        {
            "ticker": "BBB",
            "valuation_score": 55,
            "top_conviction_eligible": True,
            "long_growth_v1_score": 85,
        },
    ])

    detail, top10, pit, summary = compare_v1_v2_impact(
        baseline_snapshot=baseline_snapshot,
        challenger_snapshot=challenger_snapshot,
        baseline_scored=baseline_scored,
        challenger_scored=challenger_scored,
        as_of=pd.Timestamp("2026-09-15"),
    )

    beta = detail.set_index("ticker").loc["BBB"]
    assert beta["shares_coverage_change"] == "gained"
    assert beta["valuation_change"] == "gained"
    assert beta["top_conviction_change"] == "gained"
    assert beta["challenger_selection_rank"] == 1
    assert summary.baseline_shares_present == 1
    assert summary.challenger_shares_present == 2
    assert summary.shares_gained == 1
    assert summary.valuation_gained == 1
    assert summary.top_conviction_gained == 1
    assert summary.top10_overlap == 1
    assert set(top10["ticker"]) == {"AAA", "BBB"}
    assert pit.empty


def test_comparison_flags_only_dates_after_the_decision_day() -> None:
    baseline_snapshot = _snapshot([
        {
            "ticker": "AAA",
            "shares_outstanding": 100,
            "shares_outstanding_filed_date": "2026-09-15",
            "shares_outstanding_accepted_at": "2026-09-15T20:00:00",
        }
    ])
    challenger_snapshot = _snapshot([
        {
            "ticker": "AAA",
            "shares_outstanding": 100,
            "shares_outstanding_filed_date": "2026-09-16",
            "shares_outstanding_accepted_at": "2026-09-16T01:00:00",
        }
    ])
    scored = _scored([
        {
            "ticker": "AAA",
            "valuation_score": 50,
            "top_conviction_eligible": True,
            "long_growth_v1_score": 75,
        }
    ])

    _, _, pit, summary = compare_v1_v2_impact(
        baseline_snapshot=baseline_snapshot,
        challenger_snapshot=challenger_snapshot,
        baseline_scored=scored,
        challenger_scored=scored,
        as_of=pd.Timestamp("2026-09-15"),
    )

    assert len(pit) == 2
    assert set(pit["side"]) == {"challenger"}
    assert summary.pit_violations == 2


def test_impact_runner_has_no_broker_or_order_imports() -> None:
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_v2_impact_comparison.py"
    ).read_text(encoding="utf-8")

    prohibited = (
        "finance.broker",
        "finance.shadow.order_intent",
        "finance.shadow.order_review",
        "place_equity_order",
    )
    assert not any(value in source for value in prohibited)
