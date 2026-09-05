from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from finance.data.sources.sec_financial_statements import (
    load_sec_financial_statement_zip,
)


def test_loads_sec_quarter_and_filters_by_acceptance(tmp_path: Path) -> None:
    zip_path = tmp_path / "2015q1.zip"
    sub = (
        "adsh\tcik\tform\tperiod\tfiled\taccepted\n"
        "0001\t123\t10-K\t20141231\t20150220\t20150220153000\n"
        "0002\t456\t10-Q\t20150331\t20150501\t20150501120000\n"
    )
    num = (
        "adsh\ttag\tversion\tddate\tqtrs\tuom\tvalue\n"
        "0001\tRevenues\tus-gaap/2014\t20141231\t4\tUSD\t1000\n"
        "0002\tRevenues\tus-gaap/2015\t20150331\t1\tUSD\t300\n"
    )
    pre = (
        "adsh\treport\tline\tstmt\ttag\tversion\n"
        "0001\t1\t1\tIS\tRevenues\tus-gaap/2014\n"
        "0002\t1\t1\tIS\tRevenues\tus-gaap/2015\n"
    )

    with ZipFile(zip_path, "w") as archive:
        archive.writestr("sub.txt", sub)
        archive.writestr("num.txt", num)
        archive.writestr("pre.txt", pre)

    quarter = load_sec_financial_statement_zip(zip_path)
    available = quarter.facts_available_by(datetime(2015, 3, 1))

    assert len(quarter.submissions) == 2
    assert len(quarter.numeric_facts) == 2
    assert len(quarter.presentation) == 2
    assert list(available["adsh"]) == ["0001"]
    assert int(available.iloc[0]["value"]) == 1000
