from datetime import date, datetime

import pandas as pd

from finance.data.sec_snapshot import latest_facts_as_of, pivot_snapshot


def test_latest_facts_as_of_respects_acceptance_time_and_period() -> None:
    facts = pd.DataFrame(
        [
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2014, 12, 31),
                "accepted_at": datetime(2015, 2, 1, 10, 0),
                "value": 100.0,
                "source_tag": "Revenues",
            },
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2014, 12, 31),
                "accepted_at": datetime(2015, 3, 1, 10, 0),
                "value": 110.0,
                "source_tag": "Revenues",
            },
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2015, 3, 31),
                "accepted_at": datetime(2015, 5, 1, 10, 0),
                "value": 30.0,
                "source_tag": "Revenues",
            },
        ]
    )

    feb = latest_facts_as_of(facts, datetime(2015, 2, 15))
    mar = latest_facts_as_of(facts, datetime(2015, 3, 15))

    assert feb.iloc[0]["value"] == 100.0
    assert mar.iloc[0]["value"] == 110.0


def test_pivot_snapshot_creates_one_row_per_cik() -> None:
    facts = pd.DataFrame(
        [
            {"cik": 1, "concept": "revenue", "value": 100.0},
            {"cik": 1, "concept": "net_income", "value": 10.0},
            {"cik": 2, "concept": "revenue", "value": 200.0},
        ]
    )

    snapshot = pivot_snapshot(facts)

    assert len(snapshot) == 2
    assert "revenue" in snapshot.columns
    assert "net_income" in snapshot.columns
