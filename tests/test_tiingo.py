from datetime import date

import pytest

from finance.data.sources.tiingo import TiingoClient


def test_tiingo_requires_token() -> None:
    with pytest.raises(ValueError):
        TiingoClient("")


def test_coverage_records_provider_error(monkeypatch) -> None:
    client = TiingoClient("test")

    def fail(*args, **kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(client, "daily_prices", fail)
    result = client.coverage(
        "OLD", start=date(2015, 1, 1), end=date(2015, 12, 31)
    )
    assert result.covered is False
    assert result.error == "provider failure"
