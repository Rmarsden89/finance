from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from finance.factors.registry import FACTOR_REGISTRY


@dataclass(frozen=True)
class NormalizationConfig:
    lower_quantile: float = 0.01
    upper_quantile: float = 0.99
    min_cross_section: int = 20


DEFAULT_NORMALIZATION = NormalizationConfig()


def normalize_validated_factors(
    frame: pd.DataFrame,
    *,
    config: NormalizationConfig = DEFAULT_NORMALIZATION,
) -> pd.DataFrame:
    """Normalize validated factors within each weekly cross-section.

    For each factor this adds:
      <factor>_winsorized
      <factor>_winsorized_flag
      <factor>_percentile
      <factor>_score

    Scores are 0-100 with higher always better after direction adjustment.
    """

    if "decision_date" not in frame.columns:
        raise ValueError("Normalization requires decision_date")

    if not 0 <= config.lower_quantile < config.upper_quantile <= 1:
        raise ValueError("Invalid winsorization quantiles")

    result = frame.copy()
    result["decision_date"] = pd.to_datetime(
        result["decision_date"],
        errors="coerce",
    )

    for factor, definition in FACTOR_REGISTRY.items():
        validated_col = f"{factor}_validated"
        if validated_col not in result.columns:
            continue

        values = pd.to_numeric(
            result[validated_col],
            errors="coerce",
        )

        winsorized = pd.Series(np.nan, index=result.index, dtype="float64")
        winsorized_flag = pd.Series(False, index=result.index, dtype="bool")
        percentile = pd.Series(np.nan, index=result.index, dtype="float64")

        for _, idx in result.groupby("decision_date", sort=False).groups.items():
            group_values = values.loc[idx]
            finite = group_values[np.isfinite(group_values)]

            if len(finite) < config.min_cross_section:
                continue

            lower = finite.quantile(config.lower_quantile)
            upper = finite.quantile(config.upper_quantile)

            clipped = group_values.clip(lower=lower, upper=upper)
            winsorized.loc[idx] = clipped
            winsorized_flag.loc[idx] = (
                group_values.notna()
                & (
                    (group_values < lower)
                    | (group_values > upper)
                )
            )

            ranked = clipped.rank(
                method="average",
                pct=True,
                na_option="keep",
            )
            percentile.loc[idx] = ranked

        if definition.direction == "higher_is_better":
            score = percentile * 100.0
        elif definition.direction == "lower_is_better":
            score = (1.0 - percentile) * 100.0
        else:
            raise ValueError(
                f"Unknown direction for {factor}: {definition.direction}"
            )

        result[f"{factor}_winsorized"] = winsorized
        result[f"{factor}_winsorized_flag"] = winsorized_flag
        result[f"{factor}_percentile"] = percentile
        result[f"{factor}_score"] = score

    return result
