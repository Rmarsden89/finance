from email.message import Message
from io import BytesIO
import urllib.error

import pandas as pd
import pytest

from finance.data.sec_recovery import failed_tickers, merge_targeted_recovery
from finance.data.sources.sec_current import SecCurrentClient


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


def _http_error(code: int) -> urllib.error.HTTPError:
    headers = Message()
    return urllib.error.HTTPError(
        url="https://data.sec.gov/test",
        code=code,
        msg="test",
        hdrs=headers,
        fp=BytesIO(b""),
    )


def test_transient_503_retries_then_succeeds(monkeypatch):
    responses = [_http_error(503), _Response(b'{"ok": true}')]

    def fake_urlopen(request, timeout):
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    sleeps = []
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)

    client = SecCurrentClient(
        user_agent="finance-test test@example.com",
        request_delay_seconds=0,
        max_retries=2,
        retry_backoff_seconds=0.25,
    )

    assert client.get_json("https://data.sec.gov/test") == {"ok": True}
    assert client.request_count == 2
    assert client.retry_count == 1
    assert sleeps == [0.25]


def test_transient_503_exhaustion_raises(monkeypatch):
    def fake_urlopen(request, timeout):
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)

    client = SecCurrentClient(
        user_agent="finance-test test@example.com",
        request_delay_seconds=0,
        max_retries=2,
        retry_backoff_seconds=0,
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        client.get_text("https://www.sec.gov/test")

    assert exc_info.value.code == 503
    assert client.request_count == 3
    assert client.retry_count == 2


def test_non_transient_404_is_not_retried(monkeypatch):
    def fake_urlopen(request, timeout):
        raise _http_error(404)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = SecCurrentClient(
        user_agent="finance-test test@example.com",
        request_delay_seconds=0,
        max_retries=3,
        retry_backoff_seconds=0,
    )

    with pytest.raises(urllib.error.HTTPError):
        client.get_text("https://www.sec.gov/test")

    assert client.request_count == 1
    assert client.retry_count == 0


def test_targeted_recovery_replaces_only_failed_tickers():
    discovery = pd.DataFrame(
        [
            {"ticker": "AAA", "status": "new_filing_cached", "accession": "a1"},
            {"ticker": "BBB", "status": "new_filing_partial", "accession": "b1"},
            {"ticker": "CCC", "status": "submissions_error", "accession": ""},
        ]
    )
    recovery = pd.DataFrame(
        [
            {"ticker": "BBB", "status": "new_filing_cached", "accession": "b1"},
            {"ticker": "CCC", "status": "no_new_filing", "accession": ""},
        ]
    )

    assert failed_tickers(discovery) == ["BBB", "CCC"]

    merged = merge_targeted_recovery(discovery, recovery)
    by_ticker = {row.ticker: row.status for row in merged.itertuples()}

    assert by_ticker == {
        "AAA": "new_filing_cached",
        "BBB": "new_filing_cached",
        "CCC": "no_new_filing",
    }
    assert failed_tickers(merged) == []


def test_targeted_recovery_keeps_unresolved_failure_visible():
    discovery = pd.DataFrame(
        [{"ticker": "BBB", "status": "new_filing_partial", "accession": "b1"}]
    )
    recovery = pd.DataFrame(
        [{"ticker": "BBB", "status": "new_filing_partial", "accession": "b1"}]
    )

    merged = merge_targeted_recovery(discovery, recovery)
    assert failed_tickers(merged) == ["BBB"]
