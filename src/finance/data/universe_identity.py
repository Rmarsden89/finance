from __future__ import annotations

from datetime import date
from pathlib import Path

from .audit import unique_constituents
from .models import MembershipInterval
from .sources.datamule import load_datamule_identity_map
from .sources.pitindex import load_pitindex_sp500
from .sources.sec_historical_names import load_sec_historical_name_map
from .sources.sec_tickers import sec_cik_map
from .sources.ticker_aliases import load_safe_ticker_aliases


def build_enriched_sp500_intervals(
    pitindex_data: str | Path,
    *,
    sec_tickers: str | Path | None = None,
    ticker_renames: str | Path | None = None,
    datamule_dir: str | Path | None = None,
    sec_historical_names: str | Path | None = None,
    start_year: int = 2015,
) -> list[MembershipInterval]:
    """Rebuild PIT S&P intervals with the same conservative identity layers
    used by the universe audit.
    """

    pitindex_data = Path(pitindex_data)
    identity_map: dict[str, int] = {}

    if sec_tickers:
        identity_map.update(sec_cik_map(sec_tickers))

    rename_path = Path(ticker_renames) if ticker_renames else _default_rename_path(pitindex_data)
    if rename_path and rename_path.exists() and identity_map:
        identity_map.update(
            load_safe_ticker_aliases(
                rename_path,
                cik_by_ticker=identity_map,
            )
        )

    intervals = load_pitindex_sp500(
        pitindex_data,
        sec_cik_by_ticker=identity_map,
    )

    unresolved = [
        row
        for row in unique_constituents(
            intervals,
            start_date=date(start_year, 1, 1),
        )
        if row.cik is None
    ]

    if datamule_dir:
        datamule_dir = Path(datamule_dir)
        dm = load_datamule_identity_map(
            metadata_path=datamule_dir / "listed_filer_metadata.csv.gz",
            names_path=datamule_dir / "listed_filer_names.csv.gz",
            unresolved=unresolved,
        )
        identity_map.update(dm)
        intervals = load_pitindex_sp500(
            pitindex_data,
            sec_cik_by_ticker=identity_map,
        )
        unresolved = [
            row
            for row in unique_constituents(
                intervals,
                start_date=date(start_year, 1, 1),
            )
            if row.cik is None
        ]

    if sec_historical_names:
        historical = load_sec_historical_name_map(
            sec_historical_names,
            unresolved=unresolved,
        )
        identity_map.update(historical)
        intervals = load_pitindex_sp500(
            pitindex_data,
            sec_cik_by_ticker=identity_map,
        )

    return intervals


def _default_rename_path(pitindex_data: Path) -> Path | None:
    candidate = pitindex_data.parents[1] / "data" / "ticker_renames.csv"
    return candidate if candidate.exists() else None
