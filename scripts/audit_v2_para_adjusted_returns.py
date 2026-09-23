from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the known PARA adjusted-return discontinuities and compare "
            "baseline versus Issue #7 candidate data."
        )
    )
    parser.add_argument(
        "--baseline-prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
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
        "--quality-issues",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v2/market_price_quality_issues.csv"
        ),
    )
    parser.add_argument("--ticker", default="PARA")
    parser.add_argument("--threshold", type=float, default=0.75)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "para_adjusted_return_audit_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _read_prices(path: Path, ticker: str) -> pd.DataFrame:
    frame = pd.read_csv(path, compression="infer", low_memory=False)
    frame["pit_ticker"] = frame["pit_ticker"].astype(str).str.upper()
    frame = frame.loc[frame["pit_ticker"].eq(ticker.upper())].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["adjusted_close"] = pd.to_numeric(
        frame["adjusted_close"], errors="coerce"
    )
    return frame.sort_values("date", kind="stable").reset_index(drop=True)


def _add_returns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["raw_return"] = result["close"].pct_change()
    result["adjusted_return"] = result["adjusted_close"].pct_change()
    result["adjustment_ratio"] = (
        result["adjusted_close"] / result["close"]
    )
    result["adjustment_ratio_change"] = result["adjustment_ratio"].pct_change()
    return result


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    baseline_path = _resolve(root, args.baseline_prices)
    candidate_path = _resolve(root, args.candidate_prices)
    issues_path = _resolve(root, args.quality_issues)
    output_dir = _resolve(root, args.output_dir)

    required = {
        "baseline prices": baseline_path,
        "candidate prices": candidate_path,
        "quality issues": issues_path,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing PARA audit input(s):\n  " + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit("PARA audit requires a clean tracked worktree")
    if output_dir.exists():
        raise SystemExit(f"Audit output already exists; preserve it: {output_dir}")

    baseline = _add_returns(_read_prices(baseline_path, args.ticker))
    candidate = _add_returns(_read_prices(candidate_path, args.ticker))
    issues = pd.read_csv(issues_path, low_memory=False)
    issues["pit_ticker"] = issues["pit_ticker"].astype(str).str.upper()
    issues = issues.loc[issues["pit_ticker"].eq(args.ticker.upper())].copy()
    issues["date"] = pd.to_datetime(issues["date"], errors="coerce")

    compare_columns = [
        "date",
        "market_ticker",
        "close",
        "adjusted_close",
        "source",
    ]
    baseline_compare = baseline[compare_columns].rename(
        columns={c: f"{c}_baseline" for c in compare_columns if c != "date"}
    )
    candidate_compare = candidate[compare_columns].rename(
        columns={c: f"{c}_candidate" for c in compare_columns if c != "date"}
    )
    comparison = baseline_compare.merge(
        candidate_compare,
        on="date",
        how="outer",
        validate="one_to_one",
    )
    comparison["close_matches"] = (
        pd.to_numeric(comparison["close_baseline"], errors="coerce")
        .eq(pd.to_numeric(comparison["close_candidate"], errors="coerce"))
    )
    comparison["adjusted_close_matches"] = (
        pd.to_numeric(comparison["adjusted_close_baseline"], errors="coerce")
        .eq(pd.to_numeric(comparison["adjusted_close_candidate"], errors="coerce"))
    )

    flagged_dates = set(issues["date"].dropna())
    contexts = []
    for flagged in sorted(flagged_dates):
        idx = candidate.index[candidate["date"].eq(flagged)]
        if len(idx) != 1:
            continue
        pos = int(idx[0])
        left = max(0, pos - 2)
        right = min(len(candidate), pos + 3)
        block = candidate.iloc[left:right].copy()
        block["flagged_date"] = flagged
        block["is_flagged"] = block["date"].eq(flagged)
        contexts.append(block)

    context = (
        pd.concat(contexts, ignore_index=True)
        if contexts
        else pd.DataFrame(columns=candidate.columns)
    )

    flagged_rows = candidate.loc[candidate["date"].isin(flagged_dates)].copy()
    flagged_rows["raw_extreme"] = (
        flagged_rows["raw_return"].abs().gt(args.threshold)
    )
    flagged_rows["adjusted_extreme"] = (
        flagged_rows["adjusted_return"].abs().gt(args.threshold)
    )
    flagged_rows["adjustment_ratio_extreme_change"] = (
        flagged_rows["adjustment_ratio_change"].abs().gt(args.threshold)
    )
    flagged_rows["disposition"] = flagged_rows.apply(
        lambda row: (
            "adjusted_only_discontinuity"
            if bool(row["adjusted_extreme"]) and not bool(row["raw_extreme"])
            else "raw_and_adjusted_discontinuity"
            if bool(row["adjusted_extreme"]) and bool(row["raw_extreme"])
            else "not_reproduced"
        ),
        axis=1,
    )

    baseline_candidate_identical = bool(
        len(baseline) == len(candidate)
        and not comparison.empty
        and comparison["close_matches"].fillna(False).all()
        and comparison["adjusted_close_matches"].fillna(False).all()
        and comparison["date"].notna().all()
    )

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_PARA_ADJUSTED_RETURN_AUDIT_COMPLETE",
        "ticker": args.ticker.upper(),
        "baseline_rows": len(baseline),
        "candidate_rows": len(candidate),
        "candidate_changed_para_series": not baseline_candidate_identical,
        "quality_issue_rows": len(issues),
        "flagged_dates": [
            value.date().isoformat()
            for value in sorted(flagged_dates)
        ],
        "adjusted_only_discontinuities": int(
            flagged_rows["disposition"]
            .eq("adjusted_only_discontinuity")
            .sum()
        ),
        "raw_and_adjusted_discontinuities": int(
            flagged_rows["disposition"]
            .eq("raw_and_adjusted_discontinuity")
            .sum()
        ),
        "candidate_market_data_modified": False,
    }

    output_dir.mkdir(parents=True)
    comparison.to_csv(
        output_dir / "baseline_vs_candidate_para.csv", index=False
    )
    flagged_rows.to_csv(
        output_dir / "flagged_date_analysis.csv", index=False
    )
    context.to_csv(output_dir / "flagged_date_context.csv", index=False)
    issues.to_csv(output_dir / "source_quality_issues.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "direct_inputs": fingerprint_files(
                root=root,
                paths=[baseline_path, candidate_path, issues_path],
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("ISSUE #7 PARA ADJUSTED-RETURN AUDIT")
    print(f"Baseline rows:                {len(baseline):,}")
    print(f"Candidate rows:               {len(candidate):,}")
    print(
        "Candidate changed PARA:      "
        f"{'YES' if not baseline_candidate_identical else 'NO'}"
    )
    print(f"Quality issue rows:           {len(issues):,}")
    print()
    print("FLAGGED DATES")
    for row in flagged_rows.itertuples(index=False):
        print(
            f"{row.date.date().isoformat()} "
            f"close={row.close:.6g} "
            f"adj={row.adjusted_close:.6g} "
            f"raw_ret={row.raw_return:+.2%} "
            f"adj_ret={row.adjusted_return:+.2%} "
            f"adj_ratio={row.adjustment_ratio:.6g} "
            f"ratio_change={row.adjustment_ratio_change:+.2%} "
            f"{row.disposition}"
        )
    print()
    print(f"Output directory:             {output_dir}")
    print("CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
