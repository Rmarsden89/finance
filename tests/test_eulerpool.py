from datetime import date

import pytest

from finance.data.sources.eulerpool import EulerpoolClient, _date_to_millis


def test_eulerpool_requires_token() -> None:
    with pytest.raises(ValueError):
        EulerpoolClient("")


def test_resolve_isin_uses_profile_response(monkeypatch) -> None:
    client = EulerpoolClient("test")

    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url: {"isin": "US0378331005"},
    )

    assert client.resolve_isin("AAPL") == "US0378331005"
    assert client.resolve_isin("aapl") == "US0378331005"


def test_daily_prices_parses_quotes(monkeypatch) -> None:
    client = EulerpoolClient("test")
    monkeypatch.setattr(
        client,
        "resolve_isin",
        lambda ticker: "US0378331005",
    )

    timestamp_ms = _date_to_millis(
        date(2020, 1, 2),
        end_of_day=False,
    )
    monkeypatch.setattr(
        client,
        "_get_json",
        lambda url: [
            {
                "timestamp": timestamp_ms,
                "price": 75.09,
            }
        ],
    )

    rows = client.daily_prices(
        "AAPL",
        start=date(2020, 1, 2),
        end=date(2020, 1, 2),
    )

    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].date == date(2020, 1, 2)
    assert rows[0].close == 75.09
    assert rows[0].source == "eulerpool_quotes"


def test_coverage_records_provider_error(monkeypatch) -> None:
    client = EulerpoolClient("test")

    def fail(*args, **kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(client, "daily_prices", fail)
    result = client.coverage(
        "OLD",
        start=date(2015, 1, 1),
        end=date(2015, 12, 31),
    )

    assert result.covered is False
    assert result.error == "provider failure"
