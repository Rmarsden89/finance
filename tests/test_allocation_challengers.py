from __future__ import annotations

import math

from finance.research.allocation_challengers import allocation_weights


CANDIDATES = [
    (1, "A", 90.0),
    (2, "B", 80.0),
    (3, "C", 70.0),
    (4, "D", 60.0),
    (5, "E", 50.0),
    (6, "F", 40.0),
    (7, "G", 30.0),
    (8, "H", 20.0),
    (9, "I", 10.0),
    (10, "J", 5.0),
]


def _assert_normalized(weights: dict[str, float]) -> None:
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-12)
    assert all(value >= 0 for value in weights.values())


def test_equal_dollar_weights() -> None:
    weights = allocation_weights("equal_dollar", CANDIDATES)
    _assert_normalized(weights)
    assert all(math.isclose(value, 0.1) for value in weights.values())


def test_rank_weighted_uses_original_rank_after_filtering() -> None:
    filtered = [row for row in CANDIDATES if row[0] not in {1, 4}]
    weights = allocation_weights("rank_weighted", filtered)
    _assert_normalized(weights)

    # Original rank 2 keeps raw weight 9, while original rank 3 keeps 8.
    assert math.isclose(weights["B"] / weights["C"], 9 / 8)


def test_score_weighted_is_proportional_to_scores() -> None:
    weights = allocation_weights("score_weighted", CANDIDATES)
    _assert_normalized(weights)
    assert math.isclose(weights["A"] / weights["B"], 90 / 80)


def test_conviction_bands_predeclare_50_30_20_structure() -> None:
    weights = allocation_weights("conviction_bands", CANDIDATES)
    _assert_normalized(weights)

    assert math.isclose(sum(weights[t] for t in ("A", "B", "C")), 0.50)
    assert math.isclose(sum(weights[t] for t in ("D", "E", "F")), 0.30)
    assert math.isclose(sum(weights[t] for t in ("G", "H", "I", "J")), 0.20)
