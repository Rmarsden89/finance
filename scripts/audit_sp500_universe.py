from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date
from pathlib import Path

from finance.data.audit import audit_dates, unique_constituents
from finance.data.sources.datamule import load_datamule_identity_map
from finance.data.sources.historical_identity import load_identity_context
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.data.sources.sec_tickers import sec_cik_map
from finance.data.sources.ticker_aliases import load_safe_ticker_aliases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit point-in-time S&P 500 identity coverage."
    )
    parser.add_argument(
        "--pitindex-data",
        type=Path,
        required=True,
        help="Directory containing pitindex sp500_seed/current/changes CSVs.",
    )
    parser.add_argument(
        "--sec-tickers",
        type=Path,
        help="Optional SEC company_tickers.json for current ticker->CIK enrichment.",
    )
    parser.add_argument(
        "--ticker-renames",
        type=Path,
        help=(
            "Optional pitindex ticker_renames.csv. If omitted, the script tries "
            "to infer it from the cloned pitindex repository layout."
        ),
    )
    parser.add_argument(
        "--datamule-dir",
        type=Path,
        help=(
            "Optional directory containing listed_filer_metadata.csv.gz and "
            "listed_filer_names.csv.gz for SEC-derived historical identity enrichment."
        ),
    )
    parser.add_argument(
        "--unresolved-csv",
        type=Path,
        default=Path("reports/unresolved_identities.csv"),
        help="CSV output path for unresolved identity research queue.",
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    return parser.parse_args()


def _default_rename_path(pitindex_data: Path) -> Path | None:
    candidate = pitindex_data.parents[1] / "data" / "ticker_renames.csv"
    return candidate if candidate.exists() else None


def _write_unresolved_csv(
    output_path: Path,
    unresolved,
    *,
    identity_context,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "company_name",
                "membership_start",
                "membership_end",
                "category",
                "rename_successor",
                "removal_reason",
            ],
        )
        writer.writeheader()

        for row in unresolved:
            context = identity_context.get(row.ticker)
            writer.writerow(
                {
                    "ticker": row.ticker,
                    "company_name": row.company_name
                    or (context.company_name if context else "")
                    or "",
                    "membership_start": row.start_date.isoformat(),
                    "membership_end": (
                        row.end_date.isoformat() if row.end_date else ""
                    ),
                    "category": (
                        context.category
                        if context
                        else "historical_identity_research"
                    ),
                    "rename_successor": (
                        context.rename_successor if context else ""
                    )
                    or "",
                    "removal_reason": (
                        context.removal_reason if context else ""
                    )
                    or "",
                }
            )


def main() -> None:
    args = parse_args()

    sec_map = sec_cik_map(args.sec_tickers) if args.sec_tickers else {}
    rename_path = args.ticker_renames or _default_rename_path(args.pitindex_data)

    alias_map: dict[str, int] = {}
    if rename_path and sec_map:
        alias_map = load_safe_ticker_aliases(
            rename_path,
            cik_by_ticker=sec_map,
        )

    identity_map = {**sec_map, **alias_map}

    intervals = load_pitindex_sp500(
        args.pitindex_data,
        sec_cik_by_ticker=identity_map,
    )

    unique = unique_constituents(
        intervals,
        start_date=date(args.start_year, 1, 1),
    )
    unresolved = [row for row in unique if row.cik is None]

    datamule_map: dict[str, int] = {}
    if args.datamule_dir:
        metadata_path = args.datamule_dir / "listed_filer_metadata.csv.gz"
        names_path = args.datamule_dir / "listed_filer_names.csv.gz"

        if not metadata_path.exists() or not names_path.exists():
            raise FileNotFoundError(
                "Datamule directory must contain listed_filer_metadata.csv.gz "
                "and listed_filer_names.csv.gz"
            )

        datamule_map = load_datamule_identity_map(
            metadata_path=metadata_path,
            names_path=names_path,
            unresolved=unresolved,
        )

        identity_map = {**identity_map, **datamule_map}
        intervals = load_pitindex_sp500(
            args.pitindex_data,
            sec_cik_by_ticker=identity_map,
        )
        unique = unique_constituents(
            intervals,
            start_date=date(args.start_year, 1, 1),
        )
        unresolved = [row for row in unique if row.cik is None]

    snapshot_dates = [
        date(year, 1, 2)
        for year in range(args.start_year, args.end_year + 1)
    ]
    audit = audit_dates(intervals, snapshot_dates)

    changes_path = args.pitindex_data / "sp500_changes.csv"
    identity_context = load_identity_context(
        changes_path=changes_path,
        rename_path=rename_path,
    )
    _write_unresolved_csv(
        args.unresolved_csv,
        unresolved,
        identity_context=identity_context,
    )

    categories = Counter(
        (
            identity_context[row.ticker].category
            if row.ticker in identity_context
            else "historical_identity_research"
        )
        for row in unresolved
    )

    print("HISTORICAL S&P 500 UNIVERSE AUDIT")
    print(f"Start year: {args.start_year}")
    print(f"Unique tickers active since start: {len(unique)}")
    print(f"CIK resolved: {len(unique) - len(unresolved)}")
    print(f"CIK unresolved: {len(unresolved)}")
    print(f"Safe ticker aliases applied: {len(alias_map)}")
    print(f"Datamule identities applied: {len(datamule_map)}")
    print()

    print("date        members  resolved  unresolved  cik_coverage")
    for row in audit:
        print(
            f"{row.as_of}  {row.members:7d}  {row.cik_resolved:8d}  "
            f"{row.cik_unresolved:10d}  {row.cik_coverage:11.1%}"
        )

    print()
    print("UNRESOLVED IDENTITY CATEGORIES")
    for category, count in sorted(categories.items()):
        print(f"{category:30s} {count:4d}")

    print()
    print(f"Research queue written to: {args.unresolved_csv}")

    if alias_map:
        print()
        print("SAFE TICKER ALIASES USED")
        for ticker, cik in sorted(alias_map.items()):
            print(f"{ticker:8s} -> CIK {cik}")

    if datamule_map:
        print()
        print("DATAMULE IDENTITIES USED")
        for ticker, cik in sorted(datamule_map.items()):
            print(f"{ticker:8s} -> CIK {cik}")

    if unresolved:
        print()
        print("UNRESOLVED HISTORICAL TICKERS")
        for row in unresolved:
            context = identity_context.get(row.ticker)
            category = (
                context.category
                if context
                else "historical_identity_research"
            )
            print(
                f"{row.ticker:8s} "
                f"{(row.company_name or ''):32.32s} "
                f"{category:28.28s} "
                f"{row.start_date} -> {row.end_date or 'present'}"
            )


if __name__ == "__main__":
    main()
