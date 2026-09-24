from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
from typing import Any


TARGET_NAME_TERMS = ("share", "liabilit")


@dataclass(frozen=True)
class InlineFact:
    name: str
    context_ref: str
    unit_ref: str
    decimals: str
    scale: str
    sign: str
    value_text: str


@dataclass(frozen=True)
class InlineContext:
    context_id: str
    instant: str
    start_date: str
    end_date: str
    dimensions: str


class _InlineXbrlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.facts: list[InlineFact] = []
        self.contexts: dict[str, InlineContext] = {}
        self._fact_stack: list[dict[str, Any]] = []
        self._context_stack: list[dict[str, Any]] = []
        self._capture_context_field: str | None = None
        self._capture_dimension = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        attr = {str(k).lower(): (v or "") for k, v in attrs}

        if lower in {"ix:nonfraction", "ix:nonnumeric"}:
            name = attr.get("name", "")
            if any(term in name.lower() for term in TARGET_NAME_TERMS):
                self._fact_stack.append(
                    {
                        "name": name,
                        "context_ref": attr.get("contextref", ""),
                        "unit_ref": attr.get("unitref", ""),
                        "decimals": attr.get("decimals", ""),
                        "scale": attr.get("scale", ""),
                        "sign": attr.get("sign", ""),
                        "parts": [],
                    }
                )
            elif self._fact_stack:
                self._fact_stack.append({"ignore": True})
            return

        if lower in {"xbrli:context", "context"}:
            context_id = attr.get("id", "")
            self._context_stack.append(
                {
                    "context_id": context_id,
                    "instant": "",
                    "start_date": "",
                    "end_date": "",
                    "dimensions": [],
                }
            )
            return

        if self._context_stack:
            mapping = {
                "xbrli:instant": "instant",
                "instant": "instant",
                "xbrli:startdate": "start_date",
                "startdate": "start_date",
                "xbrli:enddate": "end_date",
                "enddate": "end_date",
            }
            if lower in mapping:
                self._capture_context_field = mapping[lower]
            elif lower.endswith("explicitmember"):
                self._capture_dimension = True

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in {"ix:nonfraction", "ix:nonnumeric"} and self._fact_stack:
            current = self._fact_stack.pop()
            if current.get("ignore"):
                return
            text = "".join(current["parts"]).strip()
            self.facts.append(
                InlineFact(
                    name=current["name"],
                    context_ref=current["context_ref"],
                    unit_ref=current["unit_ref"],
                    decimals=current["decimals"],
                    scale=current["scale"],
                    sign=current["sign"],
                    value_text=text,
                )
            )
            return

        if lower in {"xbrli:context", "context"} and self._context_stack:
            current = self._context_stack.pop()
            context_id = current["context_id"]
            if context_id:
                self.contexts[context_id] = InlineContext(
                    context_id=context_id,
                    instant=current["instant"],
                    start_date=current["start_date"],
                    end_date=current["end_date"],
                    dimensions="|".join(current["dimensions"]),
                )
            self._capture_context_field = None
            self._capture_dimension = False
            return

        if self._context_stack:
            if lower in {
                "xbrli:instant",
                "instant",
                "xbrli:startdate",
                "startdate",
                "xbrli:enddate",
                "enddate",
            }:
                self._capture_context_field = None
            elif lower.endswith("explicitmember"):
                self._capture_dimension = False

    def handle_data(self, data: str) -> None:
        if self._fact_stack and not self._fact_stack[-1].get("ignore"):
            self._fact_stack[-1]["parts"].append(data)

        if self._context_stack:
            text = data.strip()
            if not text:
                return
            if self._capture_context_field:
                self._context_stack[-1][self._capture_context_field] += text
            elif self._capture_dimension:
                self._context_stack[-1]["dimensions"].append(text)


def parse_inline_xbrl_evidence(html: str) -> tuple[list[InlineFact], dict[str, InlineContext]]:
    parser = _InlineXbrlParser()
    parser.feed(html)
    return parser.facts, parser.contexts


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def accession_archive_cik(accession: str) -> int:
    first = accession.split("-", 1)[0].strip()
    if not first.isdigit():
        raise ValueError(f"Invalid SEC accession: {accession!r}")
    return int(first)


def filing_document_url(*, accession: str, primary_document: str) -> str:
    cik = accession_archive_cik(accession)
    compact = accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{compact}/{primary_document}"
    )


def filing_header_url(*, accession: str) -> str:
    cik = accession_archive_cik(accession)
    compact = accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{compact}/{accession}.hdr.sgml"
    )
