from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


SUPPORTED_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}


@dataclass(frozen=True)
class FilingRecord:
    cik: int
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str | None


class SecCurrentClient:
    """Small SEC EDGAR client for current-filing evidence acquisition."""

    def __init__(
        self,
        *,
        user_agent: str,
        request_delay_seconds: float = 0.2,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        user_agent = user_agent.strip()
        if not user_agent:
            raise ValueError("SEC user agent must not be blank")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")
        self.user_agent = user_agent
        self.request_delay_seconds = request_delay_seconds
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.request_count = 0
        self.retry_count = 0

    @staticmethod
    def _is_transient_http_error(exc: urllib.error.HTTPError) -> bool:
        return exc.code == 429 or 500 <= exc.code <= 599

    def _retry_delay(self, exc: urllib.error.HTTPError, retry_index: int) -> float:
        retry_after = None
        if exc.headers is not None:
            raw = exc.headers.get("Retry-After")
            if raw:
                try:
                    retry_after = float(raw)
                except (TypeError, ValueError):
                    retry_after = None
        if retry_after is not None and retry_after >= 0:
            return retry_after
        return self.retry_backoff_seconds * (2 ** retry_index)

    def _get_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json,text/plain,*/*",
            },
        )

        for attempt in range(self.max_retries + 1):
            try:
                self.request_count += 1
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    payload = response.read()
                if self.request_delay_seconds > 0:
                    time.sleep(self.request_delay_seconds)
                return payload
            except urllib.error.HTTPError as exc:
                if not self._is_transient_http_error(exc) or attempt >= self.max_retries:
                    raise
                self.retry_count += 1
                delay = self._retry_delay(exc, attempt)
                if delay > 0:
                    time.sleep(delay)

        raise RuntimeError("unreachable SEC request retry state")

    def get_json(self, url: str) -> dict:
        return json.loads(self._get_bytes(url).decode("utf-8"))

    def get_text(self, url: str) -> str:
        return self._get_bytes(url).decode("utf-8", errors="replace")

    def submissions(self, cik: int) -> dict:
        return self.get_json(
            f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        )

    def companyfacts(self, cik: int) -> dict:
        return self.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        )

    def filing_header(self, cik: int, accession: str) -> str:
        accession_compact = accession.replace("-", "")
        return self.get_text(
            "https://www.sec.gov/Archives/edgar/data/"
            f"{cik}/{accession_compact}/{accession}.hdr.sgml"
        )


def recent_filings_from_submissions(
    payload: dict,
    *,
    supported_forms: set[str] | None = None,
) -> list[FilingRecord]:
    supported_forms = supported_forms or SUPPORTED_FORMS
    recent = ((payload.get("filings") or {}).get("recent") or {})
    accessions = recent.get("accessionNumber") or []
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    primary_documents = recent.get("primaryDocument") or []

    cik = int(str(payload.get("cik") or "0"))
    records: list[FilingRecord] = []

    for index, accession in enumerate(accessions):
        form = forms[index] if index < len(forms) else ""
        if form not in supported_forms:
            continue

        raw_filing_date = (
            filing_dates[index] if index < len(filing_dates) else ""
        )
        if not raw_filing_date:
            continue

        raw_report_date = (
            report_dates[index] if index < len(report_dates) else ""
        )
        raw_primary = (
            primary_documents[index]
            if index < len(primary_documents)
            else ""
        )

        records.append(
            FilingRecord(
                cik=cik,
                accession=accession,
                form=form,
                filing_date=date.fromisoformat(raw_filing_date),
                report_date=(
                    date.fromisoformat(raw_report_date)
                    if raw_report_date
                    else None
                ),
                primary_document=raw_primary or None,
            )
        )

    return records


_ACCEPTANCE_RE = re.compile(
    r"<ACCEPTANCE-DATETIME>\s*(\d{14})",
    flags=re.IGNORECASE,
)


def parse_acceptance_datetime(header_text: str) -> datetime:
    match = _ACCEPTANCE_RE.search(header_text)
    if not match:
        raise ValueError("SEC filing header missing ACCEPTANCE-DATETIME")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)
