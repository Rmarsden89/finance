import pandas as pd


def test_placeholder_current_family_audit_contract() -> None:
    frame = pd.DataFrame(
        {
            "financial_health_factor_count": [1, 2, 3],
            "valuation_factor_count": [0, 2, 4],
        }
    )
    assert frame["financial_health_factor_count"].tolist() == [1, 2, 3]
    assert frame["valuation_factor_count"].tolist() == [0, 2, 4]
