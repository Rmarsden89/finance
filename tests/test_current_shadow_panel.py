import pandas as pd

from finance.data.current_shadow_panel import assemble_current_scoring_panel


def test_assemble_current_panel_preserves_weekly_history_and_current_row() -> None:
    historical = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "as_of": "2025-09-12 16:00:00",
                "decision_date": "2025-09-12",
                "revenue": 100,
            },
            {
                "ticker": "AAA",
                "as_of": "2025-01-03 16:00:00",
                "decision_date": "2025-01-03",
                "revenue": 80,
            },
        ]
    )
    extension = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "as_of": "2026-09-04 16:00:00",
                "decision_date": "2026-09-04",
                "revenue": 120,
            }
        ]
    )
    current = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "as_of": "2026-09-09T15:53:29-04:00",
                "decision_date": "2026-09-09",
                "revenue": 125,
            }
        ]
    )

    combined = assemble_current_scoring_panel(
        historical,
        extension,
        current,
        lookback_days=390,
    )

    assert list(combined["revenue"]) == [100, 120, 125]
    assert len(combined) == 3
    assert combined.iloc[-1]["as_of"] == pd.Timestamp("2026-09-09 15:53:29")
    assert combined["as_of"].dt.tz is None
