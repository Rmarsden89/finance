from __future__ import annotations

import math

import pandas as pd

from finance.factors.financial_health import add_financial_health_factors
from finance.factors.quality import add_quality_factors


def test_quality_and_health_factors_use_safe_denominators() -> None:
    panel = pd.DataFrame(
        [
            {
                "net_income": 20.0,
                "total_assets": 100.0,
                "shareholders_equity": 50.0,
                "operating_income": 15.0,
                "revenue": 200.0,
                "operating_cash_flow": 30.0,
                "capital_expenditures": 10.0,
                "total_liabilities": 40.0,
                "cash": 10.0,
            },
            {
                "net_income": -5.0,
                "total_assets": 0.0,
                "shareholders_equity": -10.0,
                "operating_income": -2.0,
                "revenue": 0.0,
                "operating_cash_flow": -3.0,
                "capital_expenditures": 1.0,
                "total_liabilities": 0.0,
                "cash": 0.0,
            },
        ]
    )

    result = add_quality_factors(panel)
    result = add_financial_health_factors(result)

    assert result.loc[0, "return_on_assets"] == 0.2
    assert result.loc[0, "return_on_equity"] == 0.4
    assert result.loc[0, "operating_margin"] == 0.075
    assert result.loc[0, "free_cash_flow_margin"] == 0.1
    assert result.loc[0, "liabilities_to_assets"] == 0.4
    assert result.loc[0, "cash_to_assets"] == 0.1
    assert result.loc[0, "operating_cash_flow_to_liabilities"] == 0.75
    assert result.loc[0, "positive_operating_cash_flow"] == 1.0

    for column in (
        "return_on_assets",
        "return_on_equity",
        "operating_margin",
        "free_cash_flow_margin",
        "liabilities_to_assets",
        "cash_to_assets",
        "operating_cash_flow_to_liabilities",
    ):
        assert math.isnan(result.loc[1, column])

    assert result.loc[1, "positive_operating_cash_flow"] == 0.0
