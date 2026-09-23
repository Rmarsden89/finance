from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import math


@dataclass(frozen=True)
class AllocationRule:
    rule_id: str
    description: str


@dataclass(frozen=True)
class AllocationExperimentDefinition:
    experiment_id: str = "allocation_challengers_v1"
    weekly_contribution: float = 10.0
    top_n: int = 10
    max_addon_position_weight: float = 0.10
    discretionary_selling: bool = False
    evaluation_start_year: int = 2016
    rolling_window_years: tuple[int, ...] = (3, 5)
    leave_winner_out_count: int = 1
    score_compression_multipliers: tuple[float, ...] = (0.50, 0.75, 1.00)
    score_expansion_multipliers: tuple[float, ...] = (1.25, 1.50)
    rank_perturbation_swaps: tuple[tuple[int, int], ...] = (
        (1, 2),
        (3, 4),
        (5, 6),
        (9, 10),
    )


ALLOCATION_EXPERIMENT = AllocationExperimentDefinition()


RULES = (
    AllocationRule(
        rule_id="equal_dollar",
        description=(
            "Equal share of the deployable weekly contribution across all "
            "currently eligible selected names."
        ),
    ),
    AllocationRule(
        rule_id="rank_weighted",
        description=(
            "Linear descending rank weights N,N-1,...,1 across the selected "
            "names, renormalized over names not blocked by the position gate."
        ),
    ),
    AllocationRule(
        rule_id="score_weighted",
        description=(
            "Weights proportional to each selected name's nonnegative model "
            "score, renormalized over names not blocked by the position gate."
        ),
    ),
    AllocationRule(
        rule_id="conviction_bands",
        description=(
            "Predeclared contribution bands: ranks 1-3 receive 50% total, "
            "ranks 4-6 receive 30% total, ranks 7-10 receive 20% total; "
            "each band is equal-weighted internally and remaining eligible "
            "weights are renormalized after the position gate."
        ),
    ),
)


def allocation_weights(
    rule_id: str,
    candidates: Iterable[tuple[int, str, float]],
) -> dict[str, float]:
    """Return normalized contribution weights for ranked candidates.

    Each candidate is (rank, ticker, score). The caller is responsible for
    removing names blocked by the common concentration gate before calling
    this function.
    """

    rows = list(candidates)
    if not rows:
        return {}

    if rule_id == "equal_dollar":
        raw = {ticker: 1.0 for _, ticker, _ in rows}
    elif rule_id == "rank_weighted":
        n = len(rows)
        raw = {
            ticker: float(n - position + 1)
            for position, (_, ticker, _) in enumerate(rows, start=1)
        }
    elif rule_id == "score_weighted":
        raw = {
            ticker: max(0.0, float(score))
            if math.isfinite(float(score))
            else 0.0
            for _, ticker, score in rows
        }
        if sum(raw.values()) <= 0:
            raw = {ticker: 1.0 for _, ticker, _ in rows}
    elif rule_id == "conviction_bands":
        raw = {}
        for rank, ticker, _ in rows:
            if rank <= 3:
                raw[ticker] = 0.50 / 3.0
            elif rank <= 6:
                raw[ticker] = 0.30 / 3.0
            else:
                raw[ticker] = 0.20 / 4.0
    else:
        raise ValueError(f"Unknown allocation rule: {rule_id}")

    total = sum(raw.values())
    if total <= 0:
        raise ValueError(f"Allocation rule produced no positive weight: {rule_id}")
    return {ticker: value / total for ticker, value in raw.items()}


def experiment_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment": asdict(ALLOCATION_EXPERIMENT),
        "rules": [asdict(rule) for rule in RULES],
        "common_constraints": {
            "same_ranked_inputs_across_rules": True,
            "weekly_contribution_cap": ALLOCATION_EXPERIMENT.weekly_contribution,
            "top_n": ALLOCATION_EXPERIMENT.top_n,
            "max_addon_position_weight": (
                ALLOCATION_EXPERIMENT.max_addon_position_weight
            ),
            "discretionary_selling": False,
            "pit_universe_forced_exits_only": True,
            "research_only": True,
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
        "required_metrics": [
            "xirr",
            "time_weighted_return",
            "annualized_time_weighted_return",
            "max_drawdown",
            "concentration",
            "cash_drag",
            "turnover",
            "contribution_dispersion",
            "benchmark_relative_results",
        ],
        "required_robustness": [
            "rolling_3y",
            "rolling_5y",
            "leave_winner_out",
            "score_compression",
            "score_expansion",
            "rank_perturbation",
        ],
    }
