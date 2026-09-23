from __future__ import annotations

import pandas as pd

from scripts.audit_v2_weekly_panel_sensitivity import _top10


def test_top10_comparison_is_deterministic_on_score_and_ticker() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2020-01-03",
            "ticker": f"T{n:02d}",
            "long_growth_v1_score": 100 - n,
            "top_conviction_eligible": True,
        }
        for n in range(1, 12)
    ])

    result = _top10(frame)

    key = pd.Timestamp("2020-01-03")
    assert result[key] == tuple(f"T{n:02d}" for n in range(1, 11))
