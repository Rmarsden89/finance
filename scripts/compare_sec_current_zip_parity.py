from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from finance.data.sec_current_facts import (
    extract_companyfacts_candidates,
    load_companyfacts,
)
from finance.data.sec_winners import select_canonical_winners


KEYS = ["cik", "adsh", "concept", "ddate_date", "qtrs", "uom"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare SEC companyfacts-derived candidates with quarterly-ZIP "
            "canonical winner facts on overlapping historical filings."
        )
    )
    parser.add_argument(
        "--current-candidates",
        type=Path,
        required=True,
        help=(
            "Current candidate CSV used to identify ticker/CIK pairs and cached "
            "companyfacts payloads."
        ),
    )
    parser.add_argument(
        "--winner-facts",
        type=Path,
        default=Path("data/cache/sec/sec_winner_facts_all.csv"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cache/sec/current"),
    )
    parser.add_argument(
        "--filings-per-ticker",
        type=int,
        default=3,
        help="Most recent overlapping archived filings per ticker. Default: 3.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/sec_current_zip_parity.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/sec_current_zip_parity_summary.csv"),
    )
    parser.add_argument(
        "--filing-output",
        type=Path,
        default=Path("reports/sec_current_zip_parity_filings.csv"),
    )
    return parser.parse_args()


def decimal_equal(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def restore_types(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["cik"] = pd.to_numeric(result["cik"], errors="coerce")
    result["qtrs"] = pd.to_numeric(result["qtrs"], errors="coerce")
    for column in ("ddate_date", "period_date", "filed_date"):
        if column in result.columns:
            result[column] = pd.to_datetime(result[column], errors="coerce").dt.date
    if "accepted_at" in result.columns:
        result["accepted_at"] = pd.to_datetime(result["accepted_at"], errors="coerce")
    return result


def filing_metadata(group: pd.DataFrame) -> dict:
    first = group.sort_values(["accepted_at", "concept"], kind="stable").iloc[-1]
    return {
        "cik": int(first["cik"]),
        "adsh": str(first["adsh"]),
        "form": str(first["form"]),
        "period_date": first["period_date"],
        "filed_date": first["filed_date"],
        "accepted_at": first["accepted_at"],
    }


def compare_frames(
    *,
    ticker: str,
    archived: pd.DataFrame,
    current: pd.DataFrame,
) -> list[dict]:
    archived = archived.copy()
    current = current.copy()

    def normalize(frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        if result.empty:
            return pd.DataFrame(
                columns=[
                    *KEYS,
                    "value",
                    "source_tag",
                ]
            )
        result["cik"] = pd.to_numeric(result["cik"], errors="coerce")
        result["qtrs"] = pd.to_numeric(result["qtrs"], errors="coerce")
        result["ddate_date"] = pd.to_datetime(
            result["ddate_date"], errors="coerce"
        ).dt.date
        result["uom"] = result["uom"].fillna("").astype(str)
        return result

    archived = normalize(archived)
    current = normalize(current)

    archived_groups = {
        key: group
        for key, group in archived.groupby(KEYS, dropna=False, sort=False)
    }
    current_groups = {
        key: group
        for key, group in current.groupby(KEYS, dropna=False, sort=False)
    }

    rows: list[dict] = []
    for key in sorted(
        set(archived_groups) | set(current_groups),
        key=lambda item: tuple(str(value) for value in item),
    ):
        old = archived_groups.get(key)
        new = current_groups.get(key)

        old_values = [] if old is None else sorted(
            set(str(value) for value in old["value"])
        )
        new_values = [] if new is None else sorted(
            set(str(value) for value in new["value"])
        )
        old_tags = [] if old is None else sorted(
            set(old["source_tag"].astype(str))
        )
        new_tags = [] if new is None else sorted(
            set(new["source_tag"].astype(str))
        )

        if old is None:
            status = "companyfacts_only"
        elif new is None:
            status = "zip_only"
        elif len(old_values) == 1 and len(new_values) == 1:
            if decimal_equal(old_values[0], new_values[0]):
                status = (
                    "exact_match"
                    if old_tags == new_tags
                    else "value_match_tag_difference"
                )
            else:
                status = "value_mismatch"
        else:
            status = "ambiguous_multi_value"

        rows.append(
            {
                "ticker": ticker,
                "cik": key[0],
                "adsh": key[1],
                "concept": key[2],
                "ddate_date": key[3],
                "qtrs": key[4],
                "uom": key[5],
                "status": status,
                "zip_values": "|".join(old_values),
                "companyfacts_values": "|".join(new_values),
                "zip_tags": "|".join(old_tags),
                "companyfacts_tags": "|".join(new_tags),
            }
        )

    return rows


def main() -> None:
    args = parse_args()
    if args.filings_per_ticker <= 0:
        raise SystemExit("--filings-per-ticker must be positive")

    current_seed = pd.read_csv(args.current_candidates, low_memory=False)
    current_seed["cik"] = pd.to_numeric(current_seed["cik"], errors="coerce")
    ticker_cik = (
        current_seed[["ticker", "cik"]]
        .dropna()
        .drop_duplicates()
        .sort_values("ticker")
    )

    winners = restore_types(pd.read_csv(args.winner_facts, low_memory=False))

    detail_rows: list[dict] = []
    filing_rows: list[dict] = []

    print("SEC CURRENT ↔ QUARTERLY ZIP PARITY", flush=True)
    print(f"Tickers:                   {len(ticker_cik):,}", flush=True)
    print(f"Filings per ticker:        {args.filings_per_ticker}", flush=True)

    for ticker_index, seed in enumerate(ticker_cik.itertuples(index=False), start=1):
        ticker = str(seed.ticker)
        cik = int(seed.cik)
        path = args.cache_dir / "companyfacts" / f"CIK{cik:010d}.json"

        print(
            f"[{ticker_index}/{len(ticker_cik)}] {ticker} CIK={cik}",
            flush=True,
        )

        if not path.exists():
            filing_rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "adsh": "",
                    "status": "missing_companyfacts_cache",
                    "candidate_rows": 0,
                    "winner_rows": 0,
                    "error": str(path),
                }
            )
            continue

        payload = load_companyfacts(path)
        company_name = str(payload.get("entityName") or ticker)

        archived_cik = winners.loc[winners["cik"].eq(cik)].copy()
        if archived_cik.empty:
            filing_rows.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "adsh": "",
                    "status": "no_archived_winners",
                    "candidate_rows": 0,
                    "winner_rows": 0,
                    "error": "",
                }
            )
            continue

        filing_meta = []
        for _, group in archived_cik.groupby("adsh", sort=False):
            meta = filing_metadata(group)
            if (
                pd.isna(meta["accepted_at"])
                or meta["period_date"] is None
                or meta["filed_date"] is None
            ):
                continue
            filing_meta.append(meta)

        filing_meta = sorted(
            filing_meta,
            key=lambda item: (item["accepted_at"], item["adsh"]),
            reverse=True,
        )[: args.filings_per_ticker]

        print(
            f"    overlapping archived filings selected: {len(filing_meta)}",
            flush=True,
        )

        for filing in filing_meta:
            accession = filing["adsh"]
            archived_filing = archived_cik.loc[
                archived_cik["adsh"].astype(str).eq(accession)
            ].copy()

            try:
                candidates, audit = extract_companyfacts_candidates(
                    payload,
                    accession=accession,
                    cik=cik,
                    company_name=company_name,
                    form=filing["form"],
                    report_date=filing["period_date"],
                    filed_date=filing["filed_date"],
                    accepted_at=filing["accepted_at"].isoformat(),
                )
                if candidates.empty:
                    current_winners = candidates
                    winner_audit_rows = 0
                else:
                    current_winners, _, winner_audit = select_canonical_winners(
                        candidates
                    )
                    winner_audit_rows = winner_audit.rows_output

                detail_rows.extend(
                    compare_frames(
                        ticker=ticker,
                        archived=archived_filing,
                        current=current_winners,
                    )
                )

                filing_rows.append(
                    {
                        "ticker": ticker,
                        "cik": cik,
                        "adsh": accession,
                        "status": (
                            "ok" if not current_winners.empty else "no_candidates"
                        ),
                        "candidate_rows": len(candidates),
                        "winner_rows": winner_audit_rows,
                        "error": "",
                    }
                )
                print(
                    f"    {accession} archive={len(archived_filing)} "
                    f"candidates={len(candidates)} winners={winner_audit_rows}",
                    flush=True,
                )
            except Exception as exc:
                filing_rows.append(
                    {
                        "ticker": ticker,
                        "cik": cik,
                        "adsh": accession,
                        "status": "error",
                        "candidate_rows": 0,
                        "winner_rows": 0,
                        "error": str(exc),
                    }
                )
                print(f"    {accession} ERROR: {exc}", flush=True)

    detail = pd.DataFrame(detail_rows)
    filings = pd.DataFrame(filing_rows)

    if detail.empty:
        summary = pd.DataFrame(
            columns=["status", "fact_groups", "unique_tickers", "unique_ciks"]
        )
    else:
        summary = (
            detail.groupby("status", dropna=False)
            .agg(
                fact_groups=("status", "size"),
                unique_tickers=("ticker", "nunique"),
                unique_ciks=("cik", "nunique"),
            )
            .reset_index()
            .sort_values("status")
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(args.output, index=False)
    summary.to_csv(args.summary_output, index=False)
    filings.to_csv(args.filing_output, index=False)

    print()
    print("PARITY COMPLETE", flush=True)
    for row in summary.itertuples(index=False):
        print(
            f"{row.status:30s} "
            f"groups={int(row.fact_groups):5,d} "
            f"tickers={int(row.unique_tickers):3,d}",
            flush=True,
        )
    print(f"Detail:                    {args.output}", flush=True)
    print(f"Summary:                   {args.summary_output}", flush=True)
    print(f"Filings:                   {args.filing_output}", flush=True)
    print("Canonical winner facts were NOT modified.", flush=True)


if __name__ == "__main__":
    main()
