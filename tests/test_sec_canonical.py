from datetime import date, datetime

import pandas as pd

from finance.data.sec_canonical import build_canonical_facts, facts_available_by


def test_build_canonical_facts_filters_forms_and_coreg() -> None:
    submissions = pd.DataFrame(
        [
            {
                "adsh": "1",
                "cik": 123,
                "name": "Test Co",
                "form": "10-K",
                "period_date": date(2014, 12, 31),
                "filed_date": date(2015, 2, 1),
                "accepted_at": datetime(2015, 2, 1, 12, 0),
            },
            {
                "adsh": "2",
                "cik": 123,
                "name": "Test Co",
                "form": "8-K",
                "period_date": date(2015, 1, 31),
                "filed_date": date(2015, 2, 2),
                "accepted_at": datetime(2015, 2, 2, 12, 0),
            },
        ]
    )
    numeric = pd.DataFrame(
        [
            {
                "adsh": "1",
                "tag": "Revenues",
                "value": 100.0,
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "coreg": "",
            },
            {
                "adsh": "1",
                "tag": "Revenues",
                "value": 20.0,
                "ddate_date": date(2014, 12, 31),
                "qtrs": 4,
                "uom": "USD",
                "coreg": "Segment A",
            },
            {
                "adsh": "2",
                "tag": "Revenues",
                "value": 5.0,
                "ddate_date": date(2015, 1, 31),
                "qtrs": 1,
                "uom": "USD",
                "coreg": "",
            },
        ]
    )

    canonical, audit = build_canonical_facts(submissions, numeric)

    assert len(canonical) == 1
    assert canonical.iloc[0]["value"] == 100.0
    assert audit.rows_supported_forms == 2
    assert audit.rows_consolidated == 1


def test_facts_available_by_uses_acceptance_timestamp() -> None:
    facts = pd.DataFrame(
        [
            {"accepted_at": datetime(2015, 2, 1, 12, 0), "value": 1},
            {"accepted_at": datetime(2015, 3, 1, 12, 0), "value": 2},
        ]
    )

    available = facts_available_by(facts, datetime(2015, 2, 15))

    assert list(available["value"]) == [1]
