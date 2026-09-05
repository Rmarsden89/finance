from datetime import date, datetime

import pandas as pd
import pytest

from finance.data.sec_snapshot import SecWinnerFactCursor, latest_facts_as_of


def _winner_facts() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2020, 12, 31),
                "accepted_at": datetime(2021, 2, 1, 12, 0),
                "value": 100.0,
                "source_tag": "Revenues",
            },
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2021, 3, 31),
                "accepted_at": datetime(2021, 5, 1, 12, 0),
                "value": 120.0,
                "source_tag": "Revenues",
            },
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2021, 3, 31),
                "accepted_at": datetime(2021, 5, 15, 12, 0),
                "value": 121.0,
                "source_tag": "Revenues",
            },
        ]
    )


def test_sec_winner_fact_cursor_matches_snapshot_logic() -> None:
    facts = _winner_facts()
    cursor = SecWinnerFactCursor(facts)

    first_as_of = datetime(2021, 5, 10, 16, 0)
    cursor_first = cursor.as_of(first_as_of)
    expected_first = latest_facts_as_of(facts, first_as_of)

    assert cursor_first.iloc[0]["value"] == expected_first.iloc[0]["value"] == 120.0

    second_as_of = datetime(2021, 5, 20, 16, 0)
    cursor_second = cursor.as_of(second_as_of)
    expected_second = latest_facts_as_of(facts, second_as_of)

    assert cursor_second.iloc[0]["value"] == expected_second.iloc[0]["value"] == 121.0


def test_sec_winner_fact_cursor_rejects_time_travel() -> None:
    cursor = SecWinnerFactCursor(_winner_facts())
    cursor.as_of(datetime(2021, 5, 20, 16, 0))

    with pytest.raises(ValueError):
        cursor.as_of(datetime(2021, 5, 10, 16, 0))
