from datetime import date

import pytest

from finance.data import MembershipInterval
from finance.data.membership import MembershipStore


def test_membership_is_point_in_time_and_end_date_is_exclusive() -> None:
    store = MembershipStore(
        [
            MembershipInterval("sp500", "AAA", date(2015, 1, 1), None, cik=1),
            MembershipInterval("sp500", "BBB", date(2015, 6, 1), date(2016, 1, 1), cik=2),
        ]
    )

    assert [m.ticker for m in store.members_as_of(date(2015, 5, 31))] == ["AAA"]
    assert [m.ticker for m in store.members_as_of(date(2015, 6, 1))] == ["AAA", "BBB"]
    assert [m.ticker for m in store.members_as_of(date(2016, 1, 1))] == ["AAA"]


def test_coverage_reports_unresolved_ciks() -> None:
    store = MembershipStore(
        [
            MembershipInterval("sp500", "AAA", date(2015, 1, 1), None, cik=1),
            MembershipInterval("sp500", "OLD", date(2015, 1, 1), None, cik=None),
        ]
    )

    assert store.coverage(date(2015, 2, 1)) == {
        "members": 2,
        "cik_resolved": 1,
        "cik_unresolved": 1,
        "cik_coverage": 0.5,
    }


def test_overlapping_ticker_intervals_are_rejected() -> None:
    with pytest.raises(ValueError):
        MembershipStore(
            [
                MembershipInterval("sp500", "AAA", date(2015, 1, 1), date(2016, 1, 1)),
                MembershipInterval("sp500", "AAA", date(2015, 6, 1), None),
            ]
        )
