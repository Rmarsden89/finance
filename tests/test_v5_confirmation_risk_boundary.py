import pandas as pd
import pytest

from scripts.analyze_v5_confirmation_risk_boundary import (
    add_cross_sectional_quintiles,
    interaction_severity,
)


def test_interaction_severity_mid_extreme() -> None:
    result = interaction_severity(
        pd.Series([75.0]),
        pd.Series([25.0]),
        50.0,
    )
    assert result.iloc[0] == pytest.approx(0.25)


def test_cross_sectional_quintiles_assign_bottom_and_top() -> None:
    frame = pd.DataFrame({
        "decision_date": ["2026-01-02"] * 5,
        "momentum_score": [10, 20, 30, 40, 50],
    })
    result = add_cross_sectional_quintiles(frame, "momentum")
    assert result.iloc[0]["momentum_quintile"] == 1
    assert result.iloc[-1]["momentum_quintile"] == 5
