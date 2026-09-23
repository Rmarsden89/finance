from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd

from finance.data.sources.pitindex import load_pitindex_sp500
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.price_coverage_audit import membership_days_by_ticker_and_year


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build and validate an isolated Issue #7 canonical market-data "
            "candidate using the segment-aware Tiingo path."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument("--stooq-archive", type=Path, required=True)
    parser.add_argument(
        "--tiingo-cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument(
        "--baseline-coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument(
        "--stooq-exclusions",
        type=Path,
        default=Path("data/reference/stooq_quality_exclusions.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _run(command: list[str], *, root: Path) -> None:
    print()
    print(" ".join(command), flush=True)
    subprocess.run(command, cwd=root, check=True)


def _coverage_lookup(path: Path) -> dict[str, dict[str, str]]:
    frame = pd.read_csv(path, low_memory=False)
    frame["pit_ticker"] = frame["pit_ticker"].astype(str).str.upper()
    return {
        row["pit_ticker"]: {key: str(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    }


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    python = sys.executable

    pitindex = args.pitindex_data.resolve()
    stooq = args.stooq_archive.resolve()
    tiingo_cache = _resolve(root, args.tiingo_cache_dir)
    baseline_coverage = _resolve(root, args.baseline_coverage)
    historical_tickers = _resolve(root, args.historical_market_tickers)
    exclusions = _resolve(root, args.stooq_exclusions)
    out = _resolve(root, args.output_dir)

    required = {
        "PITIndex": pitindex,
        "Stooq archive": stooq,
        "Tiingo cache": tiingo_cache,
        "baseline coverage": baseline_coverage,
        "historical ticker overrides": historical_tickers,
        "Stooq exclusions": exclusions,
    }
    missing = [f"{k}: {v}" for k, v in required.items() if not v.exists()]
    if missing:
        raise SystemExit("Missing candidate-build input(s):\n  " + "\n  ".join(missing))

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit("Issue #7 candidate build requires a clean tracked worktree")
    if out.exists():
        raise SystemExit(f"Candidate output already exists; preserve it: {out}")

    out.mkdir(parents=True)
    candidate_coverage = out / "price_coverage.csv"
    candidate_prices = out / "daily_prices.csv.gz"
    validation_log = out / "validation.txt"
    quality = out / "market_price_quality_issues.csv"

    _run([
        python,
        "scripts/build_canonical_market_data.py",
        "--pitindex-data", str(pitindex),
        "--tiingo-cache-dir", str(tiingo_cache),
        "--stooq-archive", str(stooq),
        "--historical-market-tickers", str(historical_tickers),
        "--stooq-exclusions", str(exclusions),
        "--start-year", str(args.start_year),
        "--end-year", str(args.end_year),
        "--coverage-output", str(candidate_coverage),
        "--prices-output", str(candidate_prices),
    ], root=root)

    validation = subprocess.run(
        [
            python,
            "scripts/validate_canonical_market_data.py",
            "--pitindex-data", str(pitindex),
            "--coverage", str(candidate_coverage),
            "--prices", str(candidate_prices),
            "--start-year", str(args.start_year),
            "--end-year", str(args.end_year),
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    validation_log.write_text(
        validation.stdout + ("\nSTDERR\n" + validation.stderr if validation.stderr else ""),
        encoding="utf-8",
    )
    print(validation.stdout, end="")
    if validation.returncode != 0:
        raise SystemExit(
            f"Candidate canonical validation failed; inspect {validation_log}"
        )

    _run([
        python,
        "scripts/audit_market_price_quality.py",
        "--prices", str(candidate_prices),
        "--coverage", str(candidate_coverage),
        "--tiingo-cache-dir", str(tiingo_cache),
        "--output", str(quality),
    ], root=root)

    intervals = load_pitindex_sp500(pitindex)
    membership_days, yearly_days = membership_days_by_ticker_and_year(
        intervals,
        start=date(args.start_year, 1, 1),
        end=date(args.end_year, 12, 31),
    )
    baseline = _coverage_lookup(baseline_coverage)
    candidate = _coverage_lookup(candidate_coverage)

    rows = []
    yearly = []
    for ticker, days in membership_days.items():
        before = baseline.get(ticker, {})
        after = candidate.get(ticker, {})
        before_unresolved = before.get("selected_source") == "unresolved"
        after_unresolved = after.get("selected_source") == "unresolved"
        if before_unresolved != after_unresolved or (
            before.get("selected_source") != after.get("selected_source")
        ):
            rows.append({
                "pit_ticker": ticker,
                "membership_days": days,
                "before_source": before.get("selected_source", ""),
                "after_source": after.get("selected_source", ""),
                "before_status": before.get("selected_status", ""),
                "after_status": after.get("selected_status", ""),
                "candidate_tiingo_market_tickers": after.get(
                    "tiingo_market_tickers", ""
                ),
            })
        for year, year_days in yearly_days.get(ticker, {}).items():
            yearly.append({
                "year": year,
                "membership_days": year_days,
                "before_unresolved_days": year_days if before_unresolved else 0,
                "after_unresolved_days": year_days if after_unresolved else 0,
            })

    change = pd.DataFrame(rows)
    yearly_frame = pd.DataFrame(yearly)
    yearly_summary = (
        yearly_frame.groupby("year", as_index=False)
        .agg(
            membership_days=("membership_days", "sum"),
            before_unresolved_days=("before_unresolved_days", "sum"),
            after_unresolved_days=("after_unresolved_days", "sum"),
        )
    )
    yearly_summary["before_unresolved_pct"] = (
        yearly_summary["before_unresolved_days"] / yearly_summary["membership_days"]
    )
    yearly_summary["after_unresolved_pct"] = (
        yearly_summary["after_unresolved_days"] / yearly_summary["membership_days"]
    )
    yearly_summary["recovered_days"] = (
        yearly_summary["before_unresolved_days"]
        - yearly_summary["after_unresolved_days"]
    )

    total_days = sum(membership_days.values())
    before_days = sum(
        days
        for ticker, days in membership_days.items()
        if baseline.get(ticker, {}).get("selected_source") == "unresolved"
    )
    after_days = sum(
        days
        for ticker, days in membership_days.items()
        if candidate.get(ticker, {}).get("selected_source") == "unresolved"
    )

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_CANONICAL_CANDIDATE_VALIDATED",
        "validation_passed": True,
        "total_membership_days": total_days,
        "before_unresolved_days": before_days,
        "after_unresolved_days": after_days,
        "recovered_membership_days": before_days - after_days,
        "before_unresolved_pct": before_days / total_days if total_days else 0.0,
        "after_unresolved_pct": after_days / total_days if total_days else 0.0,
        "changed_tickers": len(change),
        "canonical_market_data_modified": False,
        "candidate_only": True,
        "partial_source_stitching_changed": False,
    }

    change.to_csv(out / "coverage_changes.csv", index=False)
    yearly_summary.to_csv(out / "coverage_by_year_before_after.csv", index=False)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "direct_inputs": fingerprint_files(
                root=root,
                paths=[baseline_coverage, historical_tickers, exclusions],
            ),
            "pitindex_inputs": fingerprint_files(
                root=pitindex,
                paths=[
                    pitindex / "sp500_seed.csv",
                    pitindex / "sp500_changes.csv",
                    pitindex / "sp500_current.csv",
                ],
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("ISSUE #7 CANONICAL CANDIDATE VALIDATED")
    print(f"Before unresolved:           {before_days:,} ({before_days/total_days:.3%})")
    print(f"After unresolved:            {after_days:,} ({after_days/total_days:.3%})")
    print(f"Recovered membership-days:   {before_days - after_days:,}")
    print(f"Changed tickers:              {len(change):,}")
    print(f"Candidate directory:          {out}")
    print("PRODUCTION CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
