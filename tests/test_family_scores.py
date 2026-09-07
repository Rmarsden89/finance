from __future__ import annotations

import math

import pandas as pd

from finance.scoring.family_scores import add_family_scores


def test_family_scores_reweight_available_components() -> None:
    frame = pd.DataFrame(
        [
            {
                "return_on_assets_score": 80.0,
                "return_on_equity_score": 60.0,
                "operating_margin_score": 40.0,
                "free_cash_flow_margin_score": float("nan"),
                "liabilities_to_assets_score": 70.0,
                "cash_to_assets_score": 50.0,
                "operating_cash_flow_to_liabilities_score": float("nan"),
                "revenue_growth_1y_score": 90.0,
                "net_income_growth_1y_score": 70.0,
                "operating_income_growth_1y_score": float("nan"),
                "operating_cash_flow_growth_1y_score": 50.0,
                "positive_operating_cash_flow_validated": 1.0,
                "earnings_yield_annual_score": 80.0,
                "sales_yield_annual_score": 60.0,
                "free_cash_flow_yield_annual_score": float("nan"),
                "book_to_market_score": 40.0,
                "volatility_52w_score": 80.0,
                "downside_deviation_52w_score": 70.0,
                "max_drawdown_52w_score": float("nan"),
                "momentum_12m_ex_1m_score": 80.0,
                "momentum_6m_ex_1m_score": 60.0,
            }
        ]
    )

    result = add_family_scores(frame)

    expected_quality = (
        80.0 * 0.35
        + 60.0 * 0.15
        + 40.0 * 0.25
    ) / (0.35 + 0.15 + 0.25)

    expected_health = (
        70.0 * 0.35
        + 50.0 * 0.30
    ) / (0.35 + 0.30)

    expected_growth = (
        90.0 * 0.30
        + 70.0 * 0.20
        + 50.0 * 0.30
    ) / (0.30 + 0.20 + 0.30)

    expected_valuation = (
        80.0 * 0.30
        + 60.0 * 0.20
        + 40.0 * 0.20
    ) / (0.30 + 0.20 + 0.20)

    expected_stability = (
        80.0 * 0.35
        + 70.0 * 0.35
    ) / (0.35 + 0.35)

    expected_momentum = (
        80.0 * 0.60
        + 60.0 * 0.40
    )

    assert math.isclose(result.loc[0, "quality_score"], expected_quality)
    assert math.isclose(result.loc[0, "financial_health_score"], expected_health)
    assert math.isclose(result.loc[0, "growth_score"], expected_growth)
    assert math.isclose(result.loc[0, "valuation_score"], expected_valuation)
    assert math.isclose(result.loc[0, "stability_score"], expected_stability)
    assert math.isclose(result.loc[0, "momentum_score"], expected_momentum)

    assert result.loc[0, "quality_factor_count"] == 3
    assert result.loc[0, "financial_health_factor_count"] == 2
    assert result.loc[0, "growth_factor_count"] == 3
    assert result.loc[0, "valuation_factor_count"] == 3
    assert result.loc[0, "stability_factor_count"] == 2
    assert result.loc[0, "momentum_factor_count"] == 2
    assert result.loc[0, "positive_operating_cash_flow_flag"] == 1.0


def test_family_score_requires_minimum_components() -> None:
    frame = pd.DataFrame(
        [
            {
                "return_on_assets_score": 80.0,
                "return_on_equity_score": float("nan"),
                "operating_margin_score": float("nan"),
                "free_cash_flow_margin_score": float("nan"),
                "liabilities_to_assets_score": 70.0,
                "cash_to_assets_score": float("nan"),
                "operating_cash_flow_to_liabilities_score": float("nan"),
                "revenue_growth_1y_score": 90.0,
                "net_income_growth_1y_score": float("nan"),
                "operating_income_growth_1y_score": float("nan"),
                "operating_cash_flow_growth_1y_score": float("nan"),
                "earnings_yield_annual_score": 90.0,
                "sales_yield_annual_score": float("nan"),
                "free_cash_flow_yield_annual_score": float("nan"),
                "book_to_market_score": float("nan"),
                "volatility_52w_score": 90.0,
                "downside_deviation_52w_score": float("nan"),
                "max_drawdown_52w_score": float("nan"),
                "momentum_12m_ex_1m_score": float("nan"),
                "momentum_6m_ex_1m_score": float("nan"),
            }
        ]
    )

    result = add_family_scores(frame)

    assert pd.isna(result.loc[0, "quality_score"])
    assert pd.isna(result.loc[0, "financial_health_score"])
    assert pd.isna(result.loc[0, "growth_score"])
    assert pd.isna(result.loc[0, "valuation_score"])
    assert pd.isna(result.loc[0, "stability_score"])
    assert pd.isna(result.loc[0, "momentum_score"])
    assert not result.loc[0, "quality_eligible"]
    assert not result.loc[0, "financial_health_eligible"]
    assert not result.loc[0, "growth_eligible"]
    assert not result.loc[0, "valuation_eligible"]
    assert not result.loc[0, "stability_eligible"]
    assert not result.loc[0, "momentum_eligible"]
