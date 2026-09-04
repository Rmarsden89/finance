from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


@dataclass(frozen=True)
class SecQuarter:
    submissions: pd.DataFrame
    numeric_facts: pd.DataFrame
    presentation: pd.DataFrame | None = None

    def facts_available_by(self, as_of: datetime) -> pd.DataFrame:
        """Return numeric facts whose filing acceptance time was available by as_of."""
        accepted = self.submissions[["adsh", "accepted_at"]]
        merged = self.numeric_facts.merge(accepted, on="adsh", how="inner")
        return merged.loc[merged["accepted_at"] <= as_of].copy()


def load_sec_financial_statement_zip(path: str | Path) -> SecQuarter:
    """Load SEC Financial Statement Data Set sub.txt + num.txt from a quarterly ZIP."""
    path = Path(path)
    with ZipFile(path) as archive:
        submissions = _read_tsv(archive, "sub.txt")
        numeric = _read_tsv(archive, "num.txt")
        presentation = _read_tsv(archive, "pre.txt")

    submissions.columns = [column.lower() for column in submissions.columns]
    numeric.columns = [column.lower() for column in numeric.columns]
    presentation.columns = [column.lower() for column in presentation.columns]

    required_sub = {"adsh", "cik", "form", "period", "filed", "accepted"}
    required_num = {"adsh", "tag", "version", "ddate", "qtrs", "uom", "value"}
    required_pre = {"adsh", "report", "line", "stmt", "tag", "version"}
    _require_columns(submissions, required_sub, "sub.txt")
    _require_columns(numeric, required_num, "num.txt")
    _require_columns(presentation, required_pre, "pre.txt")

    submissions = submissions.copy()
    numeric = numeric.copy()
    presentation = presentation.copy()

    submissions["cik"] = pd.to_numeric(submissions["cik"], errors="coerce").astype("Int64")
    submissions["period_date"] = submissions["period"].map(_parse_yyyymmdd_date)
    submissions["filed_date"] = submissions["filed"].map(_parse_yyyymmdd_date)
    submissions["accepted_at"] = submissions["accepted"].map(_parse_accepted_datetime)

    numeric["ddate_date"] = numeric["ddate"].map(_parse_yyyymmdd_date)
    numeric["qtrs"] = pd.to_numeric(numeric["qtrs"], errors="coerce").astype("Int64")
    numeric["value"] = pd.to_numeric(numeric["value"], errors="coerce")

    return SecQuarter(
        submissions=submissions,
        numeric_facts=numeric,
        presentation=presentation,
    )


def _read_tsv(archive: ZipFile, name: str) -> pd.DataFrame:
    try:
        with archive.open(name) as handle:
            return pd.read_csv(handle, sep="\t", dtype=str, low_memory=False)
    except KeyError as exc:
        raise ValueError(f"SEC ZIP missing required file: {name}") from exc


def _require_columns(frame: pd.DataFrame, required: set[str], source: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{source} missing required columns: {', '.join(missing)}")


def _parse_yyyymmdd_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None
    return datetime.strptime(text, "%Y%m%d").date()


def _parse_accepted_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 14:
        return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    if len(digits) == 8:
        return datetime.strptime(digits, "%Y%m%d")
    raise ValueError(f"Unsupported SEC accepted timestamp: {value!r}")
