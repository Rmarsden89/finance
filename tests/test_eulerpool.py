from datetime import date

import pytest

from finance.data.sources.eulerpool import EulerpoolClient, _extract_bars


def test_eulerpool_requires_token() -> None:
    with pytest.raises(ValueError):
        EulerpoolClient("")


def test_extracts_documented_data_wrapper() -> None:
    payload = {
        "ticker": "AAPL",
        "data": [{"date": "2020-01-02", "close": 75.09}],
    }
    rows = _extract_bars(payload)
    assert rows[0]["close"] == 75.09


def test_coverage_records_provider_error(monkeypatch) -> None:
    client = EulerpoolClient("test")

    def fail(*args, **kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(client, "daily_prices", fail)
    result = client.coverage(
        "OLD", start=date(2015, 1, 1), end=date(2015, 12, 31)
    )
    assert result.covered is False
    assert result.error == "provider failure"
