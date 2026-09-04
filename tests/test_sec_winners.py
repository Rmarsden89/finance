from datetime import date, datetime

import pandas as pd

from finance.data.sec_winners import select_canonical_winners


def test_prefers_first_configured_tag_when_values_conflict() -> None:
    facts = pd.DataFrame(
        [
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "accepted_at": datetime(2015, 2, 1, 12, 0),
                "value": 100.0,
                "source_tag": "Revenues",
            },
            {
                "cik": 1,
                "concept": "revenue",
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "accepted_at": datetime(2015, 2, 1, 12, 0),
                "value": 90.0,
                "source_tag": "SalesRevenueNet",
            },
        ]
    )

    winners, audit, summary = select_canonical_winners(facts)

    assert winners.iloc[0]["source_tag"] == "Revenues"
    assert winners.iloc[0]["value"] == 100.0
    assert summary.groups_resolved_by_tag_priority == 1
    assert audit.iloc[0]["resolution"] == "tag_priority"


def test_same_value_duplicate_collapses_safely() -> None:
    facts = pd.DataFrame(
        [
            {
                "cik": 1,
                "concept": "net_income",
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "accepted_at": datetime(2015, 2, 1, 12, 0),
                "value": 10.0,
                "source_tag": "NetIncomeLoss",
            },
            {
                "cik": 1,
                "concept": "net_income",
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "accepted_at": datetime(2015, 2, 1, 12, 0),
                "value": 10.0,
                "source_tag": "ProfitLoss",
            },
        ]
    )

    winners, _, summary = select_canonical_winners(facts)

    assert len(winners) == 1
    assert winners.iloc[0]["source_tag"] == "NetIncomeLoss"
    assert summary.groups_resolved_by_same_value == 1
