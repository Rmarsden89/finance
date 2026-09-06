from finance.factors.registry import FACTOR_REGISTRY


def test_v1_factor_registry_has_expected_families_and_directions() -> None:
    assert FACTOR_REGISTRY["return_on_assets"].family == "quality"
    assert (
        FACTOR_REGISTRY["liabilities_to_assets"].direction
        == "lower_is_better"
    )
    assert FACTOR_REGISTRY["revenue_growth_1y"].lookback_weeks == 52
