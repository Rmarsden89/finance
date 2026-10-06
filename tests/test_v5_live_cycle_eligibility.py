from datetime import date

from scripts.audit_v5_live_cycle_eligibility import summarize_runs


def test_same_week_duplicate_counts_once() -> None:
    result = summarize_runs(
        [
            date(2026, 9, 21),
            date(2026, 9, 25),
            date(2026, 9, 28),
            date(2026, 10, 5),
            date(2026, 10, 12),
        ],
        minimum_weeks=4,
        target_weeks=5,
    )
    assert result['completed_runs'] == 5
    assert result['distinct_iso_weeks'] == 4
    assert result['entry_condition_satisfied'] is True
    assert result['target_reached'] is False
    assert result['duplicate_iso_weeks'] == {
        '2026-W39': ['2026-09-21', '2026-09-25']
    }
