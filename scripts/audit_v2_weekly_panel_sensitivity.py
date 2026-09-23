from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.data.research_panel import CanonicalPriceStore
from finance.research.fingerprints import fingerprint_files, git_provenance
from finance.research.price_coverage_audit import yearly_panel_coverage
from finance.research.v2_impact import score_long_growth_panel


PRICE_COLUMNS = [
    "market_ticker_used",
    "price_source",
    "price_date",
    "close",
    "adjusted_close",
    "return_price",
    "return_price_basis",
    "price_available",
    "price_age_days",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reprice the frozen historical weekly research panel with the "
            "validated Issue #7 candidate market data and compare V1 impact."
        )
    )
    parser.add_argument(
        "--baseline-panel",
        type=Path,
        default=Path("reports/weekly_research_panel_2015_2025.csv"),
    )
    parser.add_argument(
        "--candidate-prices",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v2/daily_prices.csv.gz"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "weekly_panel_sensitivity_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _top10(frame: pd.DataFrame) -> dict[pd.Timestamp, tuple[str, ...]]:
    data = frame.copy()
    data["decision_date"] = pd.to_datetime(
        data["decision_date"], errors="raise"
    ).dt.normalize()
    data["long_growth_v1_score"] = pd.to_numeric(
        data["long_growth_v1_score"], errors="coerce"
    )

    result: dict[pd.Timestamp, tuple[str, ...]] = {}
    for decision_date, group in data.groupby("decision_date", sort=True):
        selected = group.loc[
            group["top_conviction_eligible"].fillna(False).astype(bool)
            & group["long_growth_v1_score"].notna()
        ].sort_values(
            ["long_growth_v1_score", "ticker"],
            ascending=[False, True],
            kind="stable",
        ).head(10)
        result[decision_date] = tuple(
            selected["ticker"].astype(str).str.upper()
        )
    return result


def _reprice_panel(
    baseline: pd.DataFrame,
    *,
    candidate_prices: Path,
) -> pd.DataFrame:
    result = baseline.copy()
    store = CanonicalPriceStore(candidate_prices)

    replacement_rows = []
    for row in result[["decision_date", "ticker"]].itertuples(index=False):
        decision_date = pd.Timestamp(row.decision_date).date()
        ticker = str(row.ticker).upper()
        quote = store.latest_as_of(ticker, decision_date)

        adjusted_close = quote["adjusted_close"] if quote else None
        close = quote["close"] if quote else None
        if adjusted_close is not None:
            return_price = adjusted_close
            return_basis = "adjusted_close"
        elif close is not None:
            return_price = close
            return_basis = "close_fallback"
        else:
            return_price = None
            return_basis = None

        replacement_rows.append({
            "market_ticker_used": quote["market_ticker"] if quote else None,
            "price_source": quote["source"] if quote else None,
            "price_date": quote["date"] if quote else None,
            "close": close,
            "adjusted_close": adjusted_close,
            "return_price": return_price,
            "return_price_basis": return_basis,
            "price_available": quote is not None,
            "price_age_days": (
                (decision_date - quote["date"]).days if quote else None
            ),
        })

    replacement = pd.DataFrame(replacement_rows, index=result.index)
    for column in PRICE_COLUMNS:
        result[column] = replacement[column]

    result["research_ready"] = (
        result["identity_resolved"].fillna(False).astype(bool)
        & result["price_available"].fillna(False).astype(bool)
        & result["fundamentals_available"].fillna(False).astype(bool)
    )
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    baseline_path = _resolve(root, args.baseline_panel)
    candidate_prices = _resolve(root, args.candidate_prices)
    output_dir = _resolve(root, args.output_dir)

    required = {
        "baseline panel": baseline_path,
        "candidate prices": candidate_prices,
    }
    missing = [f"{k}: {v}" for k, v in required.items() if not v.exists()]
    if missing:
        raise SystemExit(
            "Missing weekly-panel sensitivity input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #7 weekly-panel sensitivity requires a clean tracked worktree"
        )
    if output_dir.exists():
        raise SystemExit(f"Output already exists; preserve it: {output_dir}")

    baseline = pd.read_csv(baseline_path, low_memory=False)
    candidate = _reprice_panel(
        baseline,
        candidate_prices=candidate_prices,
    )

    baseline_coverage = yearly_panel_coverage(baseline)
    candidate_coverage = yearly_panel_coverage(candidate)
    coverage = baseline_coverage.merge(
        candidate_coverage,
        on="year",
        suffixes=("_before", "_after"),
        validate="one_to_one",
    )
    coverage["price_available_gain"] = (
        coverage["price_available_rows_after"]
        - coverage["price_available_rows_before"]
    )
    coverage["research_ready_gain"] = (
        coverage["research_ready_rows_after"]
        - coverage["research_ready_rows_before"]
    )

    price_before = baseline["price_available"].fillna(False).astype(bool)
    price_after = candidate["price_available"].fillna(False).astype(bool)
    ready_before = baseline["research_ready"].fillna(False).astype(bool)
    ready_after = candidate["research_ready"].fillna(False).astype(bool)

    gained = candidate.loc[
        (~price_before & price_after) | (~ready_before & ready_after),
        [
            "decision_date",
            "ticker",
            "cik",
            "company_name",
            "price_available",
            "research_ready",
            "price_source",
            "market_ticker_used",
            "price_date",
            "close",
            "adjusted_close",
        ],
    ].copy()

    common_priced = price_before & price_after
    unexpected_price_changes = pd.DataFrame()
    if common_priced.any():
        compare = pd.DataFrame({
            "decision_date": baseline.loc[common_priced, "decision_date"],
            "ticker": baseline.loc[common_priced, "ticker"],
            "close_before": pd.to_numeric(
                baseline.loc[common_priced, "close"], errors="coerce"
            ),
            "close_after": pd.to_numeric(
                candidate.loc[common_priced, "close"], errors="coerce"
            ),
            "adjusted_before": pd.to_numeric(
                baseline.loc[common_priced, "adjusted_close"], errors="coerce"
            ),
            "adjusted_after": pd.to_numeric(
                candidate.loc[common_priced, "adjusted_close"], errors="coerce"
            ),
        })
        close_equal = (
            compare["close_before"].eq(compare["close_after"])
            | (
                compare["close_before"].isna()
                & compare["close_after"].isna()
            )
        )
        adj_equal = (
            compare["adjusted_before"].eq(compare["adjusted_after"])
            | (
                compare["adjusted_before"].isna()
                & compare["adjusted_after"].isna()
            )
        )
        unexpected_price_changes = compare.loc[
            ~(close_equal & adj_equal)
        ].copy()

    print("Scoring baseline V1...", flush=True)
    baseline_scored = score_long_growth_panel(baseline)
    print("Scoring candidate-priced V1...", flush=True)
    candidate_scored = score_long_growth_panel(candidate)

    baseline_top = _top10(baseline_scored)
    candidate_top = _top10(candidate_scored)
    weekly_rows = []
    for decision_date in sorted(set(baseline_top) | set(candidate_top)):
        before = baseline_top.get(decision_date, tuple())
        after = candidate_top.get(decision_date, tuple())
        weekly_rows.append({
            "decision_date": decision_date.date().isoformat(),
            "before_top10": "|".join(before),
            "after_top10": "|".join(after),
            "top10_overlap": len(set(before) & set(after)),
            "changed": before != after,
            "entered": "|".join(sorted(set(after) - set(before))),
            "exited": "|".join(sorted(set(before) - set(after))),
        })
    weekly = pd.DataFrame(weekly_rows)

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_WEEKLY_PANEL_SENSITIVITY_COMPLETE",
        "panel_rows": len(baseline),
        "decision_dates": int(
            pd.to_datetime(baseline["decision_date"]).nunique()
        ),
        "price_available_gained_rows": int((~price_before & price_after).sum()),
        "research_ready_gained_rows": int((~ready_before & ready_after).sum()),
        "price_available_lost_rows": int((price_before & ~price_after).sum()),
        "research_ready_lost_rows": int((ready_before & ~ready_after).sum()),
        "unexpected_existing_price_changes": len(unexpected_price_changes),
        "v1_top10_changed_weeks": int(weekly["changed"].sum()),
        "v1_top10_total_weeks": len(weekly),
        "v1_top10_min_overlap": int(weekly["top10_overlap"].min()),
        "v1_top10_mean_overlap": float(weekly["top10_overlap"].mean()),
        "candidate_market_data_modified": False,
    }

    output_dir.mkdir(parents=True)
    candidate.to_csv(output_dir / "candidate_weekly_panel.csv", index=False)
    coverage.to_csv(output_dir / "coverage_by_year_before_after.csv", index=False)
    gained.to_csv(output_dir / "newly_available_rows.csv", index=False)
    unexpected_price_changes.to_csv(
        output_dir / "unexpected_existing_price_changes.csv", index=False
    )
    weekly.to_csv(output_dir / "v1_top10_weekly_comparison.csv", index=False)
    baseline_scored.to_csv(output_dir / "baseline_v1_scored.csv", index=False)
    candidate_scored.to_csv(output_dir / "candidate_v1_scored.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "inputs": fingerprint_files(
                root=root,
                paths=[baseline_path, candidate_prices],
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    all_row = coverage.loc[coverage["year"].astype(str).eq("ALL")].iloc[0]

    print()
    print("ISSUE #7 WEEKLY PANEL SENSITIVITY")
    print(f"Panel rows:                   {len(baseline):,}")
    print(
        f"Price-available rows:         "
        f"{int(all_row.price_available_rows_before):,} -> "
        f"{int(all_row.price_available_rows_after):,} "
        f"(+{int(all_row.price_available_gain):,})"
    )
    print(
        f"Research-ready rows:          "
        f"{int(all_row.research_ready_rows_before):,} -> "
        f"{int(all_row.research_ready_rows_after):,} "
        f"(+{int(all_row.research_ready_gain):,})"
    )
    print(
        f"Unexpected existing prices:   "
        f"{len(unexpected_price_changes):,}"
    )
    print(
        f"V1 Top-10 changed weeks:      "
        f"{int(weekly['changed'].sum()):,}/{len(weekly):,}"
    )
    print(
        f"V1 Top-10 min / mean overlap: "
        f"{int(weekly['top10_overlap'].min())}/10 / "
        f"{weekly['top10_overlap'].mean():.3f}/10"
    )
    print()
    print("NEWLY AVAILABLE TICKERS")
    if gained.empty:
        print("none")
    else:
        grouped = (
            gained.groupby("ticker", as_index=False)
            .agg(
                rows=("ticker", "size"),
                first_decision=("decision_date", "min"),
                last_decision=("decision_date", "max"),
            )
            .sort_values(["rows", "ticker"], ascending=[False, True])
        )
        for row in grouped.itertuples(index=False):
            print(
                f"{row.ticker:<6} rows={int(row.rows):4d} "
                f"{row.first_decision}->{row.last_decision}"
            )
    print()
    print(f"Output directory:             {output_dir}")
    print("PRODUCTION CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
