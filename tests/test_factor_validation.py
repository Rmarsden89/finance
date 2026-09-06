from __future__ import annotations

import pandas as pd

from finance.factors.validation import (
    ValidationThresholds,
    validate_raw_factors,
)


def test_factor_validation_rejects_scale_mismatch_without_overwriting_raw() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": "MDT",
                "decision_date": "2025-01-03",
                "return_on_assets": -805.978,
                "liabilities_to_assets": 804.35,
                "cash_to_assets": 0.1,
                "return_on_equity": 0.2,
                "operating_margin": 0.1,
                "free_cash_flow_margin": 0.1,
                "operating_cash_flow_to_liabilities": 0.1,
                "positive_operating_cash_flow": 1.0,
                "revenue_growth_1y": 0.1,
                "net_income_growth_1y": 0.1,
                "operating_income_growth_1y": 0.1,
                "operating_cash_flow_growth_1y": 0.1,
                "total_assets": 46000.0,
                "total_liabilities": 37000000.0,
                "cash": 1000.0,
                "shareholders_equity": 9000.0,
                "revenue": 1000000.0,
                "net_income": -37075000.0,
                "operating_income": 100000.0,
                "operating_cash_flow": 100000.0,
                "revenue_prior_52w": 900000.0,
                "net_income_prior_52w": -100000.0,
                "operating_income_prior_52w": 90000.0,
                "operating_cash_flow_prior_52w": 90000.0,
                "growth_lookback_valid": True,
            }
        ]
    )

    result = validate_raw_factors(frame)

    assert result.loc[0, "return_on_assets"] == -805.978
    assert not result.loc[0, "return_on_assets_valid"]
    assert pd.isna(result.loc[0, "return_on_assets_validated"])
    assert (
        "total_liabilities_scale_mismatch_vs_total_assets"
        in result.loc[0, "return_on_assets_invalid_reason"]
    )


def test_factor_validation_rejects_tiny_prior_growth_scale() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": "AMCR",
                "decision_date": "2025-01-03",
                "return_on_assets": 0.1,
                "return_on_equity": 0.2,
                "operating_margin": 0.1,
                "free_cash_flow_margin": 0.1,
                "liabilities_to_assets": 0.5,
                "cash_to_assets": 0.1,
                "operating_cash_flow_to_liabilities": 0.2,
                "positive_operating_cash_flow": 1.0,
                "revenue_growth_1y": 0.05,
                "net_income_growth_1y": 18005882.0,
                "operating_income_growth_1y": 0.1,
                "operating_cash_flow_growth_1y": 0.1,
                "total_assets": 10000000000.0,
                "total_liabilities": 5000000000.0,
                "cash": 1000000000.0,
                "shareholders_equity": 5000000000.0,
                "revenue": 12000000000.0,
                "net_income": 612200000.0,
                "operating_income": 1000000000.0,
                "operating_cash_flow": 900000000.0,
                "revenue_prior_52w": 11500000000.0,
                "net_income_prior_52w": -34.0,
                "operating_income_prior_52w": 900000000.0,
                "operating_cash_flow_prior_52w": 800000000.0,
                "growth_lookback_valid": True,
            }
        ]
    )

    result = validate_raw_factors(frame)

    assert not result.loc[0, "net_income_growth_1y_valid"]
    assert pd.isna(result.loc[0, "net_income_growth_1y_validated"])
    assert (
        "prior_value_tiny_vs_company_scale"
        in result.loc[0, "net_income_growth_1y_invalid_reason"]
    )


def test_factor_validation_preserves_legitimate_extreme() -> None:
    frame = pd.DataFrame(
        [
            {
                "ticker": "CCL",
                "decision_date": "2021-01-01",
                "return_on_assets": -0.25,
                "return_on_equity": 0.3,
                "operating_margin": -3.0,
                "free_cash_flow_margin": -4.0,
                "liabilities_to_assets": 0.85,
                "cash_to_assets": 0.12,
                "operating_cash_flow_to_liabilities": -0.15,
                "positive_operating_cash_flow": 0.0,
                "revenue_growth_1y": -0.8,
                "net_income_growth_1y": -3.0,
                "operating_income_growth_1y": -4.0,
                "operating_cash_flow_growth_1y": -2.0,
                "total_assets": 50000000000.0,
                "total_liabilities": 42500000000.0,
                "cash": 6000000000.0,
                "shareholders_equity": 7500000000.0,
                "revenue": 5000000000.0,
                "net_income": -10000000000.0,
                "operating_income": -15000000000.0,
                "operating_cash_flow": -6000000000.0,
                "revenue_prior_52w": 25000000000.0,
                "net_income_prior_52w": 3000000000.0,
                "operating_income_prior_52w": 4000000000.0,
                "operating_cash_flow_prior_52w": 5000000000.0,
                "growth_lookback_valid": True,
            }
        ]
    )

    result = validate_raw_factors(
        frame,
        thresholds=ValidationThresholds(),
    )

    assert result.loc[0, "operating_margin_valid"]
    assert result.loc[0, "free_cash_flow_margin_valid"]
    assert result.loc[0, "revenue_growth_1y_valid"]
