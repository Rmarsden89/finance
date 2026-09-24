from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path

import pandas as pd

from finance.data.sources.historical_identity import load_identity_context
from finance.data.sources.pitindex import load_pitindex_sp500
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.price_coverage_audit import membership_days_by_ticker_and_year


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify remaining Issue #7 unresolved price names by PITIndex "
            "exit/removal context without inferring unsupported corporate events."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--candidate-coverage",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v2/price_coverage.csv"
        ),
    )
    parser.add_argument(
        "--provider-resolution",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "provider_resolution_v2/provider_resolution_detail.csv"
        ),
    )
    parser.add_argument(
        "--historical-market-tickers",
        type=Path,
        default=Path("data/reference/historical_market_ticker_overrides.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "exit_category_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _default_rename_path(pitindex_data: Path) -> Path | None:
    candidate = pitindex_data.parents[1] / "data" / "ticker_renames.csv"
    return candidate if candidate.exists() else None


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    pitindex = args.pitindex_data.resolve()
    candidate_coverage = _resolve(root, args.candidate_coverage)
    provider_resolution = _resolve(root, args.provider_resolution)
    market_overrides = _resolve(root, args.historical_market_tickers)
    output_dir = _resolve(root, args.output_dir)

    changes_path = pitindex / "sp500_changes.csv"
    rename_path = _default_rename_path(pitindex)

    required = {
        "candidate coverage": candidate_coverage,
        "provider resolution": provider_resolution,
        "PITIndex changes": changes_path,
        "historical market ticker overrides": market_overrides,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing Issue #7 exit-category input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #7 exit-category audit requires a clean tracked worktree"
        )
    if output_dir.exists():
        raise SystemExit(f"Output already exists; preserve it: {output_dir}")

    coverage = pd.read_csv(candidate_coverage, low_memory=False)
    coverage["pit_ticker"] = coverage["pit_ticker"].astype(str).str.upper()
    unresolved = coverage.loc[
        coverage["selected_source"].astype(str).eq("unresolved")
    ].copy()

    intervals = load_pitindex_sp500(pitindex)
    membership_days, _ = membership_days_by_ticker_and_year(
        intervals,
        start=date(args.start_year, 1, 1),
        end=date(args.end_year, 12, 31),
    )
    open_at_end = {
        interval.ticker.upper()
        for interval in intervals
        if interval.start_date <= date(args.end_year, 12, 31)
        and (
            interval.end_date is None
            or interval.end_date > date(args.end_year, 12, 31)
        )
    }

    identity_context = load_identity_context(
        changes_path=changes_path,
        rename_path=rename_path,
    )

    provider = pd.read_csv(provider_resolution, low_memory=False)
    provider["pit_ticker"] = provider["pit_ticker"].astype(str).str.upper()
    provider_map = {
        row["pit_ticker"]: row
        for row in provider.to_dict("records")
    }

    market = pd.read_csv(market_overrides, low_memory=False)
    market["pit_ticker"] = market["pit_ticker"].astype(str).str.upper()
    market_map = (
        market.groupby("pit_ticker")["market_ticker"]
        .apply(lambda values: "|".join(sorted(set(values.astype(str).str.upper()))))
        .to_dict()
    )

    rows: list[dict[str, object]] = []
    for row in unresolved.to_dict("records"):
        ticker = str(row["pit_ticker"]).upper()
        context = identity_context.get(ticker)
        provider_row = provider_map.get(ticker, {})

        if ticker in open_at_end:
            exit_category = "active_at_period_end"
            removal_reason = ""
            rename_successor = ""
            removal_company_name = ""
        elif context is not None:
            exit_category = context.category
            removal_reason = context.removal_reason or ""
            rename_successor = context.rename_successor or ""
            removal_company_name = context.company_name or ""
        else:
            exit_category = "removed_reason_context_missing"
            removal_reason = ""
            rename_successor = ""
            removal_company_name = ""

        rows.append({
            "pit_ticker": ticker,
            "membership_days": int(membership_days.get(ticker, 0)),
            "exit_category": exit_category,
            "removal_reason": removal_reason,
            "rename_successor": rename_successor,
            "removal_company_name": removal_company_name,
            "historical_market_tickers": market_map.get(ticker, ""),
            "candidate_selected_status": _text(row.get("selected_status")),
            "candidate_tiingo_status": _text(row.get("tiingo_status")),
            "candidate_stooq_status": _text(row.get("stooq_status")),
            "provider_resolution_state": _text(
                provider_row.get("resolution_state")
            ),
            "provider_attempt_status": _text(
                provider_row.get("priority_audit_status")
            ),
            "provider_attempt_error": _text(
                provider_row.get("priority_audit_error")
            ),
            "active_at_period_end": ticker in open_at_end,
            "rename_file_present": rename_path is not None,
        })

    detail = pd.DataFrame(rows).sort_values(
        ["membership_days", "pit_ticker"],
        ascending=[False, True],
        kind="stable",
    )

    summary = (
        detail.groupby("exit_category", as_index=False)
        .agg(
            tickers=("pit_ticker", "nunique"),
            membership_days=("membership_days", "sum"),
        )
        .sort_values(
            ["membership_days", "exit_category"],
            ascending=[False, True],
            kind="stable",
        )
    )
    total_days = int(detail["membership_days"].sum())
    summary["share_of_unresolved_days"] = (
        summary["membership_days"] / total_days
        if total_days
        else 0.0
    )

    provider_summary = (
        detail.groupby("provider_resolution_state", dropna=False, as_index=False)
        .agg(
            tickers=("pit_ticker", "nunique"),
            membership_days=("membership_days", "sum"),
        )
        .sort_values(
            ["membership_days", "provider_resolution_state"],
            ascending=[False, True],
            kind="stable",
        )
    )
    provider_summary["share_of_unresolved_days"] = (
        provider_summary["membership_days"] / total_days
        if total_days
        else 0.0
    )

    summary_json = {
        "schema_version": 1,
        "status": "ISSUE7_EXIT_CATEGORY_AUDIT_COMPLETE",
        "remaining_unresolved_tickers": len(detail),
        "remaining_unresolved_membership_days": total_days,
        "exit_categories": {
            row.exit_category: {
                "tickers": int(row.tickers),
                "membership_days": int(row.membership_days),
                "share_of_unresolved_days": float(
                    row.share_of_unresolved_days
                ),
            }
            for row in summary.itertuples(index=False)
        },
        "rename_context_file": (
            str(rename_path) if rename_path is not None else None
        ),
        "classification_policy": (
            "PITIndex removal reason / rename evidence; "
            "active_at_period_end for open memberships; "
            "no unsupported corporate-event inference"
        ),
        "canonical_market_data_modified": False,
    }

    output_dir.mkdir(parents=True)
    detail.to_csv(output_dir / "unresolved_exit_categories.csv", index=False)
    summary.to_csv(output_dir / "exit_category_summary.csv", index=False)
    provider_summary.to_csv(
        output_dir / "provider_resolution_summary.csv",
        index=False,
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary_json, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    fingerprint_paths = [
        candidate_coverage,
        provider_resolution,
        changes_path,
        market_overrides,
    ]
    if rename_path is not None:
        fingerprint_paths.append(rename_path)
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "inputs": fingerprint_files(
                root=root,
                paths=fingerprint_paths,
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("ISSUE #7 REMAINING UNRESOLVED EXIT CATEGORIES")
    print(f"Remaining unresolved tickers: {len(detail):,}")
    print(f"Unresolved membership-days:   {total_days:,}")
    print()
    print("EXIT CATEGORIES")
    for row in summary.itertuples(index=False):
        print(
            f"{row.exit_category:<34} "
            f"tickers={int(row.tickers):3d} "
            f"days={int(row.membership_days):6,d} "
            f"share={row.share_of_unresolved_days:6.2%}"
        )
    print()
    print("TOP UNRESOLVED DETAIL")
    for rank, row in enumerate(detail.head(25).itertuples(index=False), start=1):
        reason = row.removal_reason or "-"
        print(
            f"{rank:2d}. {row.pit_ticker:<6} "
            f"days={int(row.membership_days):5,d} "
            f"exit={row.exit_category} "
            f"provider={row.provider_resolution_state or '-'} "
            f"reason={reason}"
        )
    print()
    print(f"Output directory:             {output_dir}")
    print("CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
