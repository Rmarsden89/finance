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
            {
                "cik": 1,
                "concept": "revenue",
                "value": 100.0,
                "ddate_date": date(2014, 12, 31),
                "period_date": date(2014, 12, 31),
                "filed_date": date(2015, 2, 1),
                "accepted_at": datetime(2015, 2, 1, 12, 0),
                "form": "10-K",
                "fy": 2014,
                "fp": "FY",
                "qtrs": 4,
                "source_tag": "Revenues",
                "adsh": "0001",
            },
            {
                "cik": 1,
                "concept": "net_income",
                "value": 10.0,
                "ddate_date": date(2014, 12, 31),
                "accepted_at": datetime(2015, 2, 1, 12, 0),
                "source_tag": "NetIncomeLoss",
            },
            {
                "cik": 2,
                "concept": "revenue",
                "value": 200.0,
                "ddate_date": date(2014, 12, 31),
                "accepted_at": datetime(2015, 2, 2, 12, 0),
                "source_tag": "Revenues",
            },
        ]
    )

    snapshot = pivot_snapshot(facts)

    assert len(snapshot) == 2
    assert "revenue" in snapshot.columns
    assert "net_income" in snapshot.columns

    cik1 = snapshot.loc[snapshot["cik"].eq(1)].iloc[0]
    assert cik1["revenue"] == 100.0
    assert cik1["revenue_period_date"] == date(2014, 12, 31)
    assert cik1["revenue_filing_period_date"] == date(2014, 12, 31)
    assert cik1["revenue_filed_date"] == date(2015, 2, 1)
    assert cik1["revenue_accepted_at"] == datetime(2015, 2, 1, 12, 0)
    assert cik1["revenue_form"] == "10-K"
    assert cik1["revenue_fy"] == 2014
    assert cik1["revenue_fp"] == "FY"
    assert cik1["revenue_qtrs"] == 4
    assert cik1["revenue_source_tag"] == "Revenues"
    assert cik1["revenue_adsh"] == "0001"
