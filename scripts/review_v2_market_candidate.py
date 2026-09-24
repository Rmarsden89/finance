from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from finance.research.fingerprints import fingerprint_files, git_provenance


EXPECTED_CACHED_RECOVERIES = {"BF.B", "BRK.B", "DXC", "BHGE", "WYND"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Review the validated Issue #7 candidate against the production "
            "baseline and explain every changed ticker."
        )
    )
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v1"
        ),
    )
    parser.add_argument(
        "--baseline-coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _clean(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    candidate_dir = _resolve(root, args.candidate_dir)
    baseline_path = _resolve(root, args.baseline_coverage)
    candidate_path = candidate_dir / "price_coverage.csv"
    change_path = candidate_dir / "coverage_changes.csv"
    quality_path = candidate_dir / "market_price_quality_issues.csv"

    required = {
        "baseline coverage": baseline_path,
        "candidate coverage": candidate_path,
        "candidate changes": change_path,
        "candidate price-quality issues": quality_path,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing Issue #7 candidate review input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #7 candidate review requires a clean tracked worktree"
        )

    baseline = pd.read_csv(baseline_path, low_memory=False)
    candidate = pd.read_csv(candidate_path, low_memory=False)
    changes = pd.read_csv(change_path, low_memory=False)
    quality = pd.read_csv(quality_path, low_memory=False)

    for frame in (baseline, candidate):
        frame["pit_ticker"] = frame["pit_ticker"].astype(str).str.upper()

    merged = baseline.merge(
        candidate,
        on="pit_ticker",
        how="outer",
        suffixes=("_before", "_after"),
        validate="one_to_one",
    )

    merged["before_source"] = merged["selected_source_before"].map(_clean)
    merged["after_source"] = merged["selected_source_after"].map(_clean)
    merged["before_status"] = merged["selected_status_before"].map(_clean)
    merged["after_status"] = merged["selected_status_after"].map(_clean)
    merged["before_unresolved"] = merged["before_source"].eq("unresolved")
    merged["after_unresolved"] = merged["after_source"].eq("unresolved")

    recovered = merged.loc[
        merged["before_unresolved"] & ~merged["after_unresolved"]
    ].copy()
    regressed = merged.loc[
        ~merged["before_unresolved"] & merged["after_unresolved"]
    ].copy()

    expected = merged.loc[
        merged["pit_ticker"].isin(EXPECTED_CACHED_RECOVERIES)
    ].copy()
    expected["outcome"] = expected.apply(
        lambda row: (
            "recovered"
            if row["before_unresolved"] and not row["after_unresolved"]
            else "still_unresolved"
            if row["after_unresolved"]
            else "already_covered"
        ),
        axis=1,
    )

    quality_tickers = set()
    if not quality.empty and "pit_ticker" in quality.columns:
        quality["pit_ticker"] = quality["pit_ticker"].astype(str).str.upper()
        quality_tickers = set(quality["pit_ticker"])

    changes["pit_ticker"] = changes["pit_ticker"].astype(str).str.upper()
    changes["quality_flagged"] = changes["pit_ticker"].isin(quality_tickers)

    unexplained_changes = changes.loc[
        ~changes["pit_ticker"].isin(EXPECTED_CACHED_RECOVERIES)
    ].copy()

    summary = {
        "schema_version": 1,
        "status": "ISSUE7_CANONICAL_CANDIDATE_REVIEW_COMPLETE",
        "changed_tickers": len(changes),
        "recovered_tickers": len(recovered),
        "regressed_tickers": len(regressed),
        "expected_cached_recoveries": sorted(EXPECTED_CACHED_RECOVERIES),
        "expected_recovered": sorted(
            expected.loc[expected["outcome"].eq("recovered"), "pit_ticker"]
            .astype(str)
            .tolist()
        ),
        "expected_still_unresolved": sorted(
            expected.loc[
                expected["outcome"].eq("still_unresolved"), "pit_ticker"
            ].astype(str).tolist()
        ),
        "quality_issue_rows": len(quality),
        "quality_issue_tickers": sorted(quality_tickers),
        "high_severity_quality_issues": int(
            quality["severity"].astype(str).eq("high").sum()
        ) if not quality.empty and "severity" in quality.columns else 0,
        "canonical_market_data_modified": False,
    }

    review_dir = candidate_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=False)

    recovered.to_csv(review_dir / "recovered_tickers.csv", index=False)
    regressed.to_csv(review_dir / "regressed_tickers.csv", index=False)
    expected.to_csv(
        review_dir / "expected_cached_recovery_outcomes.csv",
        index=False,
    )
    changes.to_csv(review_dir / "all_coverage_changes.csv", index=False)
    unexplained_changes.to_csv(
        review_dir / "other_changed_tickers.csv",
        index=False,
    )
    quality.to_csv(
        review_dir / "price_quality_issues.csv",
        index=False,
    )
    (review_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (review_dir / "input_fingerprints.json").write_text(
        json.dumps({
            "schema_version": 1,
            "direct_inputs": fingerprint_files(
                root=root,
                paths=[
                    baseline_path,
                    candidate_path,
                    change_path,
                    quality_path,
                ],
            ),
            "code": provenance,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("ISSUE #7 CANONICAL CANDIDATE REVIEW")
    print(f"Changed tickers:             {len(changes):,}")
    print(f"Recovered tickers:           {len(recovered):,}")
    print(f"Regressed tickers:           {len(regressed):,}")
    print()
    print("EXPECTED CACHED RECOVERY OUTCOMES")
    for row in expected.sort_values("pit_ticker").itertuples(index=False):
        print(
            f"{row.pit_ticker:<6} {row.outcome:<18} "
            f"{row.before_source}/{row.before_status} -> "
            f"{row.after_source}/{row.after_status}"
        )

    print()
    print("ALL COVERAGE CHANGES")
    for row in changes.sort_values("membership_days", ascending=False).itertuples(index=False):
        print(
            f"{row.pit_ticker:<6} days={int(row.membership_days):5,d} "
            f"{row.before_source}/{row.before_status} -> "
            f"{row.after_source}/{row.after_status}"
            f"{'  QUALITY_FLAGGED' if row.quality_flagged else ''}"
        )

    print()
    print("PRICE QUALITY ISSUES")
    if quality.empty:
        print("none")
    else:
        for row in quality.itertuples(index=False):
            print(
                f"{row.pit_ticker:<6} {row.severity:<6} "
                f"{row.issue_type:<28} {row.date} {row.detail}"
            )

    print()
    print(f"Review directory:            {review_dir}")
    print("PRODUCTION CANONICAL MARKET DATA WAS NOT MODIFIED.")


if __name__ == "__main__":
    main()
