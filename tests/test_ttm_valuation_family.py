from __future__ import annotations

import math

import pandas as pd

from finance.research.ttm_valuation_family import (
    add_ttm_valuation_factors,
    add_ttm_valuation_family_score,
    normalize_ttm_valuation_factors,
)


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": "AAA",
            "cik": 1,
            "company_name": "AAA",
            "decision_date": "2026-09-15",
            "as_of": "2026-09-15T00:00:00-04:00",
            "close": 100.0,
            "shares_outstanding": 10_000_000.0,
            "total_assets": 2_000_000_000.0,
            "shareholders_equity": 500_000_000.0,
        }
    ])


def _ttm() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": "AAA",
            "cik": 1,
            "company_name": "AAA",
            "ttm_net_income": 100_000_000.0,
            "ttm_revenue": 2_000_000_000.0,
            "ttm_operating_cash_flow": 180_000_000.0,
            "ttm_capital_expenditures": 80_000_000.0,
            "ttm_free_cash_flow": 100_000_000.0,
            "ttm_cash_flow_end_date": "2026-06-30",
            "ttm_cash_flow_available_at": "2026-08-01T12:00:00",
        }
    ])


def _latest() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "cik": 1,
            "concept": "revenue",
            "available_at": "2026-08-01T12:00:00",
            "ttm_end_date": "2026-06-30",
        },
        {
            "cik": 1,
            "concept": "net_income",
            "available_at": "2026-08-01T12:00:00",
            "ttm_end_date": "2026-06-30",
        },
    ])


def test_ttm_valuation_uses_current_market_cap_and_ttm_numerators() -> None:
    result = add_ttm_valuation_factors(_snapshot(), _ttm(), _latest())

    assert result.loc[0, "market_cap_ttm"] == 1_000_000_000.0
    assert math.isclose(result.loc[0, "earnings_yield_ttm"], 0.10)
    assert math.isclose(result.loc[0, "sales_yield_ttm"], 2.0)
    assert math.isclose(result.loc[0, "free_cash_flow_yield_ttm"], 0.10)
    assert result.loc[0, "earnings_yield_ttm_valid"]
    assert result.loc[0, "sales_yield_ttm_valid"]
    assert result.loc[0, "free_cash_flow_yield_ttm_valid"]


def test_ttm_valuation_rejects_future_available_fact() -> None:
    latest = _latest()
    latest.loc[
        latest["concept"].eq("net_income"), "available_at"
    ] = "2026-09-16T12:00:00"

    result = add_ttm_valuation_factors(_snapshot(), _ttm(), latest)

    assert not result.loc[0, "earnings_yield_ttm_valid"]
    assert "ttm_available_after_decision" in result.loc[
        0, "earnings_yield_ttm_invalid_reason"
    ]


def test_ttm_family_reuses_existing_book_score_and_weights() -> None:
    rows = []
    for i in range(25):
        rows.append(
            {
                "ticker": f"T{i:02d}",
                "cik": i + 1,
                "decision_date": "2026-09-15",
                "earnings_yield_ttm_validated": float(i + 1),
                "sales_yield_ttm_validated": float(i + 2),
                "free_cash_flow_yield_ttm_validated": float(i + 3),
                "book_to_market_score": 50.0,
            }
        )
    frame = pd.DataFrame(rows)
    normalized = normalize_ttm_valuation_factors(frame)
    scored = add_ttm_valuation_family_score(normalized)

    assert scored["ttm_valuation_eligible"].all()
    assert scored["ttm_valuation_factor_count"].eq(4).all()
    expected = (
        scored.loc[0, "earnings_yield_ttm_score"] * 0.30
        + scored.loc[0, "sales_yield_ttm_score"] * 0.20
        + scored.loc[0, "free_cash_flow_yield_ttm_score"] * 0.30
        + 50.0 * 0.20
    )
    assert math.isclose(scored.loc[0, "ttm_valuation_score"], expected)


def test_ttm_valuation_preserves_snapshot_decision_metadata_on_merge() -> None:
    snapshot = _snapshot()
    historical_ttm = _ttm().assign(
        decision_date="2026-09-15",
        as_of="2026-09-15T00:00:00-04:00",
    )

    result = add_ttm_valuation_factors(
        snapshot,
        historical_ttm,
        _latest(),
    )

    assert "decision_date" in result.columns
    assert "as_of" in result.columns
    assert "decision_date_x" not in result.columns
    assert "decision_date_y" not in result.columns
    assert "as_of_x" not in result.columns
    assert "as_of_y" not in result.columns
    assert result.loc[0, "earnings_yield_ttm_valid"]


def test_historical_ttm_provenance_columns_are_not_suffixed_or_lost() -> None:
    snapshot = _snapshot()
    historical_ttm = _ttm().assign(
        decision_date="2026-09-15",
        as_of="2026-09-15T00:00:00-04:00",
        ttm_revenue_available_at="2026-08-01T12:00:00",
        ttm_revenue_end_date="2026-06-30",
        ttm_net_income_available_at="2026-08-01T12:00:00",
        ttm_net_income_end_date="2026-06-30",
    )

    result = add_ttm_valuation_factors(
        snapshot,
        historical_ttm,
        _latest(),
    )

    for column in (
        "ttm_revenue_available_at",
        "ttm_revenue_end_date",
        "ttm_net_income_available_at",
        "ttm_net_income_end_date",
    ):
        assert column in result.columns
        assert f"{column}_x" not in result.columns
        assert f"{column}_y" not in result.columns

    assert result.loc[0, "earnings_yield_ttm_valid"]
    assert result.loc[0, "sales_yield_ttm_valid"]
