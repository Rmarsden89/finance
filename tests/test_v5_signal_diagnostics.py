import pandas as pd

from scripts.analyze_v5_signal_diagnostics import momentum_flip_summary


def test_momentum_flip_summary_counts_direct_extreme_flips() -> None:
    frame = pd.DataFrame([
        {"ticker":"AAA","decision_date":"2026-01-01","cand003_rank":5,"momentum_quintile":1},
        {"ticker":"AAA","decision_date":"2026-01-08","cand003_rank":5,"momentum_quintile":5},
        {"ticker":"AAA","decision_date":"2026-01-15","cand003_rank":5,"momentum_quintile":4},
        {"ticker":"AAA","decision_date":"2026-01-22","cand003_rank":5,"momentum_quintile":1},
    ])
    result = momentum_flip_summary(frame)
    assert result.iloc[0]["direct_weak_strong_flips"] == 1


from scripts.analyze_v5_signal_diagnostics import add_cross_sectional_quintiles


def test_self_contained_quintile_helper() -> None:
    frame = pd.DataFrame({
        "decision_date": ["2026-01-01"] * 5,
        "momentum_score": [10, 20, 30, 40, 50],
    })
    result = add_cross_sectional_quintiles(frame, "momentum")
    assert result.iloc[0]["momentum_quintile"] == 1
    assert result.iloc[-1]["momentum_quintile"] == 5
