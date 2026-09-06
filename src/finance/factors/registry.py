from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    family: str
    direction: str
    description: str
    required_columns: tuple[str, ...]
    lookback_weeks: int = 0
    version_introduced: str = "v1"


FACTOR_REGISTRY: dict[str, FactorDefinition] = {
    "return_on_assets": FactorDefinition(
        name="return_on_assets",
        family="quality",
        direction="higher_is_better",
        description="Net income divided by total assets.",
        required_columns=("net_income", "total_assets"),
    ),
    "return_on_equity": FactorDefinition(
        name="return_on_equity",
        family="quality",
        direction="higher_is_better",
        description=(
            "Net income divided by positive shareholders' equity; "
            "negative/zero equity is left unscored."
        ),
        required_columns=("net_income", "shareholders_equity"),
    ),
    "operating_margin": FactorDefinition(
        name="operating_margin",
        family="quality",
        direction="higher_is_better",
        description="Operating income divided by positive revenue.",
        required_columns=("operating_income", "revenue"),
    ),
    "free_cash_flow_margin": FactorDefinition(
        name="free_cash_flow_margin",
        family="quality",
        direction="higher_is_better",
        description=(
            "(Operating cash flow - capital expenditures) divided by "
            "positive revenue."
        ),
        required_columns=(
            "operating_cash_flow",
            "capital_expenditures",
            "revenue",
        ),
    ),
    "liabilities_to_assets": FactorDefinition(
        name="liabilities_to_assets",
        family="financial_health",
        direction="lower_is_better",
        description="Total liabilities divided by positive total assets.",
        required_columns=("total_liabilities", "total_assets"),
    ),
    "cash_to_assets": FactorDefinition(
        name="cash_to_assets",
        family="financial_health",
        direction="higher_is_better",
        description="Cash divided by positive total assets.",
        required_columns=("cash", "total_assets"),
    ),
    "operating_cash_flow_to_liabilities": FactorDefinition(
        name="operating_cash_flow_to_liabilities",
        family="financial_health",
        direction="higher_is_better",
        description=(
            "Operating cash flow divided by positive total liabilities."
        ),
        required_columns=("operating_cash_flow", "total_liabilities"),
    ),
    "positive_operating_cash_flow": FactorDefinition(
        name="positive_operating_cash_flow",
        family="financial_health",
        direction="higher_is_better",
        description="1 when operating cash flow is positive, 0 otherwise.",
        required_columns=("operating_cash_flow",),
    ),
    "revenue_growth_1y": FactorDefinition(
        name="revenue_growth_1y",
        family="growth",
        direction="higher_is_better",
        description=(
            "Change in PIT-visible revenue versus roughly 52 weeks earlier, "
            "scaled by prior positive revenue."
        ),
        required_columns=("decision_date", "ticker", "revenue"),
        lookback_weeks=52,
    ),
    "net_income_growth_1y": FactorDefinition(
        name="net_income_growth_1y",
        family="growth",
        direction="higher_is_better",
        description=(
            "Change in PIT-visible net income versus roughly 52 weeks earlier, "
            "scaled by absolute prior net income."
        ),
        required_columns=("decision_date", "ticker", "net_income"),
        lookback_weeks=52,
    ),
    "operating_income_growth_1y": FactorDefinition(
        name="operating_income_growth_1y",
        family="growth",
        direction="higher_is_better",
        description=(
            "Change in PIT-visible operating income versus roughly 52 weeks "
            "earlier, scaled by absolute prior operating income."
        ),
        required_columns=("decision_date", "ticker", "operating_income"),
        lookback_weeks=52,
    ),
    "operating_cash_flow_growth_1y": FactorDefinition(
        name="operating_cash_flow_growth_1y",
        family="growth",
        direction="higher_is_better",
        description=(
            "Change in PIT-visible operating cash flow versus roughly 52 weeks "
            "earlier, scaled by absolute prior operating cash flow."
        ),
        required_columns=("decision_date", "ticker", "operating_cash_flow"),
        lookback_weeks=52,
    ),
}


def factor_names(*, family: str | None = None) -> list[str]:
    names = [
        name
        for name, definition in FACTOR_REGISTRY.items()
        if family is None or definition.family == family
    ]
    return sorted(names)
