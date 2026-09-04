from datetime import date

from finance.data.sources.yahoo import YahooClient


def test_coverage_records_provider_error(monkeypatch) -> None:
    client = YahooClient()

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
