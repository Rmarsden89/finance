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
