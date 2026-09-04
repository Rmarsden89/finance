from datetime import date

from finance.data.sources.stooq import StooqClient


def test_stooq_symbol_format() -> None:
    assert StooqClient._symbol("AAPL") == "aapl.us"


def test_stooq_requires_api_key() -> None:
    try:
        StooqClient("")
    except ValueError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("Expected missing API key to fail")


def test_coverage_records_provider_error(monkeypatch) -> None:
    client = StooqClient("test")

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
