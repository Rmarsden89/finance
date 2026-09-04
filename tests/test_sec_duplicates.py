from datetime import date, datetime

import pandas as pd

from finance.data.sec_duplicates import audit_duplicate_groups


def test_duplicate_audit_separates_same_and_conflicting_values() -> None:
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
                "value": 100.0,
                "source_tag": "SalesRevenueNet",
            },
            {
                "cik": 2,
                "concept": "net_income",
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "accepted_at": datetime(2015, 2, 2, 12, 0),
                "value": 10.0,
                "source_tag": "NetIncomeLoss",
            },
            {
                "cik": 2,
                "concept": "net_income",
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "accepted_at": datetime(2015, 2, 2, 12, 0),
                "value": 12.0,
                "source_tag": "ProfitLoss",
            },
        ]
    )

    audit, summary = audit_duplicate_groups(facts)

    assert summary.duplicate_groups == 2
    assert summary.same_value_groups == 1
    assert summary.conflicting_value_groups == 1
    assert set(audit["classification"]) == {"same_value", "conflicting_value"}
