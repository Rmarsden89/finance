from datetime import date, time

from finance.data.weekly_research_panel import weekly_decision_timestamps


def test_weekly_decision_timestamps_use_friday_close() -> None:
    timestamps = weekly_decision_timestamps(
        start=date(2021, 1, 1),
        end=date(2021, 1, 15),
    )

    assert [timestamp.date() for timestamp in timestamps] == [
        date(2021, 1, 1),
        date(2021, 1, 8),
        date(2021, 1, 15),
    ]
    assert all(timestamp.time() == time(16, 0) for timestamp in timestamps)


def test_weekly_decision_timestamps_reject_reverse_range() -> None:
    import pytest

    with pytest.raises(ValueError):
        weekly_decision_timestamps(
            start=date(2021, 1, 15),
            end=date(2021, 1, 1),
        )
