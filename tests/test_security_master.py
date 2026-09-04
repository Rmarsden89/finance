from datetime import date

from finance.data import SecurityRecord
from finance.data.security_master import SecurityMaster


def test_ticker_is_time_bounded_attribute_of_cik() -> None:
    master = SecurityMaster(
        [
            SecurityRecord(123, "OLD", ticker_valid_from=date(2015, 1, 1), ticker_valid_to=date(2018, 1, 1)),
            SecurityRecord(123, "NEW", ticker_valid_from=date(2018, 1, 1)),
        ]
    )

    assert master.resolve_ticker("OLD", date(2017, 1, 1)).cik == 123
    assert master.resolve_ticker("OLD", date(2019, 1, 1)) is None
    assert master.resolve_ticker("NEW", date(2019, 1, 1)).cik == 123
    assert [record.ticker for record in master.records_for_cik(123)] == ["OLD", "NEW"]
