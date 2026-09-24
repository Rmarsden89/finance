from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable

import pandas as pd

from finance.research.fingerprints import (
    fingerprint_files,
    git_provenance,
    sha256_file,
)


EXPECTED = {
    "coverage_rows": 754,
    "tiingo_selected": 690,
    "unresolved_tickers": 64,
    "unresolved_membership_days": 99_954,
    "recovered_membership_days": 13_718,
    "changed_tickers": 5,
    "price_rows": 1_322_131,
    "price_tickers": 690,
    "weekly_price_gain": 1_960,
    "weekly_research_ready_gain": 1_957,
    "v1_membership_changed_weeks": 0,
    "v2_membership_changed_weeks": 0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Promote the validated Issue #7 market-data candidate v2 to the "
            "canonical research baseline with automatic rollback."
        )
    )
    parser.add_argument("--pitindex-data", type=Path, required=True)
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "canonical_candidate_v2"
        ),
    )
    parser.add_argument(
        "--issue7-weekly-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "weekly_panel_sensitivity_v1"
        ),
    )
    parser.add_argument(
        "--issue7-ttm-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "ttm_model_sensitivity_v1"
        ),
    )
    parser.add_argument(
        "--baseline-panel",
        type=Path,
        default=Path("reports/weekly_research_panel_2015_2025.csv"),
    )
    parser.add_argument(
        "--canonical-coverage",
        type=Path,
        default=Path("data/market/price_coverage.csv"),
    )
    parser.add_argument(
        "--canonical-prices",
        type=Path,
        default=Path("data/market/daily_prices.csv.gz"),
    )
    parser.add_argument(
        "--tiingo-cache-dir",
        type=Path,
        default=Path("data/cache/tiingo"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue19_market_promotion/"
            "promotion_v1"
        ),
    )
    parser.add_argument(
        "--as-of",
        default="2026-09-15",
        help="Frozen TTM challenger research as-of date.",
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _run(
    command: list[str],
    *,
    root: Path,
    log_path: Path,
) -> subprocess.CompletedProcess[str]:
    print()
    print(" ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
    )
    text = completed.stdout
    if completed.stderr:
        text += ("\nSTDERR\n" if text else "STDERR\n") + completed.stderr
    log_path.write_text(text, encoding="utf-8")
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed


def _coverage_counts(path: Path) -> dict[str, int]:
    frame = pd.read_csv(path, low_memory=False)
    counts = (
        frame["selected_source"].fillna("").astype(str).value_counts().to_dict()
    )
    return {
        "coverage_rows": len(frame),
        "tiingo_selected": int(counts.get("tiingo", 0)),
        "stooq_selected": int(counts.get("stooq_bulk", 0)),
        "unresolved_tickers": int(counts.get("unresolved", 0)),
    }


def _price_counts(path: Path) -> dict[str, int]:
    rows = 0
    tickers: set[str] = set()
    opener = (
        __import__("gzip").open
        if path.suffix.lower() == ".gz"
        else open
    )
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows += 1
            ticker = str(row.get("pit_ticker", "")).strip().upper()
            if ticker:
                tickers.add(ticker)
    return {"price_rows": rows, "price_tickers": len(tickers)}


def _quality_signature(path: Path) -> list[tuple[str, ...]]:
    frame = pd.read_csv(path, low_memory=False)
    columns = [
        "pit_ticker",
        "source",
        "severity",
        "issue_type",
        "date",
        "value",
        "detail",
    ]
    if frame.empty:
        return []
    return [
        tuple("" if pd.isna(row[col]) else str(row[col]) for col in columns)
        for _, row in frame.sort_values(columns, kind="stable").iterrows()
    ]


def _require_equal(
    actual: object,
    expected: object,
    *,
    label: str,
) -> None:
    if actual != expected:
        raise RuntimeError(
            f"{label} mismatch: expected {expected!r}, got {actual!r}"
        )


def _require_close(
    actual: float,
    expected: float,
    *,
    label: str,
    tolerance: float = 1e-12,
) -> None:
    if abs(float(actual) - float(expected)) > tolerance:
        raise RuntimeError(
            f"{label} mismatch: expected {expected!r}, got {actual!r}"
        )


def _copy_verified(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    if sha256_file(source) != sha256_file(destination):
        raise RuntimeError(
            f"Copy verification failed: {source} -> {destination}"
        )


def _atomic_replace_from(source: Path, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".issue19.tmp")
    if temporary.exists():
        temporary.unlink()
    _copy_verified(source, temporary)
    os.replace(temporary, destination)
    if sha256_file(source) != sha256_file(destination):
        raise RuntimeError(
            f"Atomic replacement verification failed: {destination}"
        )


def _restore(
    *,
    rollback_coverage: Path,
    rollback_prices: Path,
    canonical_coverage: Path,
    canonical_prices: Path,
) -> None:
    _atomic_replace_from(rollback_coverage, canonical_coverage)
    _atomic_replace_from(rollback_prices, canonical_prices)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_candidate(
    *,
    candidate_coverage: Path,
    candidate_prices: Path,
    candidate_summary: Path,
    candidate_quality: Path,
    weekly_summary_path: Path,
    ttm_summary_path: Path,
) -> dict[str, object]:
    summary = _load_json(candidate_summary)
    weekly = _load_json(weekly_summary_path)
    ttm = _load_json(ttm_summary_path)

    _require_equal(
        bool(summary.get("validation_passed")),
        True,
        label="candidate validation_passed",
    )
    _require_equal(
        int(summary.get("after_unresolved_days", -1)),
        EXPECTED["unresolved_membership_days"],
        label="candidate unresolved membership-days",
    )
    _require_equal(
        int(summary.get("recovered_membership_days", -1)),
        EXPECTED["recovered_membership_days"],
        label="candidate recovered membership-days",
    )
    _require_equal(
        int(summary.get("changed_tickers", -1)),
        EXPECTED["changed_tickers"],
        label="candidate changed tickers",
    )

    coverage = _coverage_counts(candidate_coverage)
    prices = _price_counts(candidate_prices)
    for key in (
        "coverage_rows",
        "tiingo_selected",
        "unresolved_tickers",
    ):
        _require_equal(coverage[key], EXPECTED[key], label=key)
    _require_equal(
        coverage["stooq_selected"],
        0,
        label="candidate Stooq-selected count",
    )
    for key in ("price_rows", "price_tickers"):
        _require_equal(prices[key], EXPECTED[key], label=key)

    _require_equal(
        int(weekly.get("price_available_gained_rows", -1)),
        EXPECTED["weekly_price_gain"],
        label="Issue #7 weekly price gain",
    )
    _require_equal(
        int(weekly.get("research_ready_gained_rows", -1)),
        EXPECTED["weekly_research_ready_gain"],
        label="Issue #7 research-ready gain",
    )
    _require_equal(
        int(weekly.get("unexpected_existing_price_changes", -1)),
        0,
        label="Issue #7 unexpected existing price changes",
    )

    _require_equal(
        int(ttm.get("v1_selection_changed_weeks", -1)),
        EXPECTED["v1_membership_changed_weeks"],
        label="Issue #7 V1 Top-10 membership changes",
    )
    _require_equal(
        int(ttm.get("v2_selection_changed_weeks", -1)),
        EXPECTED["v2_membership_changed_weeks"],
        label="Issue #7 V2 Top-10 membership changes",
    )

    quality = _quality_signature(candidate_quality)
    _require_equal(len(quality), 3, label="candidate quality issue count")
    for row in quality:
        _require_equal(row[0], "PARA", label="quality ticker")
        _require_equal(row[2], "medium", label="quality severity")
        _require_equal(
            row[3],
            "extreme_adjacent_return",
            label="quality issue type",
        )

    return {
        "candidate_summary": summary,
        "issue7_weekly_summary": weekly,
        "issue7_ttm_summary": ttm,
        "coverage_counts": coverage,
        "price_counts": prices,
        "quality_issue_count": len(quality),
    }


def _compare_ttm_results(
    expected: dict[str, object],
    actual: dict[str, object],
) -> None:
    exact_keys = [
        "v1_selection_changed_weeks",
        "v2_selection_changed_weeks",
    ]
    float_keys = [
        "candidate_v1_xirr",
        "candidate_v2_xirr",
        "candidate_xirr_delta_v2_minus_v1",
        "candidate_v1_max_drawdown",
        "candidate_v2_max_drawdown",
        "candidate_minus_baseline_v1_xirr",
        "candidate_minus_baseline_v2_xirr",
        "candidate_minus_baseline_v1_drawdown",
        "candidate_minus_baseline_v2_drawdown",
    ]
    for key in exact_keys:
        _require_equal(
            int(actual[key]),
            int(expected[key]),
            label=f"TTM sensitivity {key}",
        )
    for key in float_keys:
        _require_close(
            float(actual[key]),
            float(expected[key]),
            label=f"TTM sensitivity {key}",
        )


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    python = sys.executable

    pitindex = args.pitindex_data.resolve()
    candidate_dir = _resolve(root, args.candidate_dir)
    issue7_weekly_dir = _resolve(root, args.issue7_weekly_dir)
    issue7_ttm_dir = _resolve(root, args.issue7_ttm_dir)
    baseline_panel = _resolve(root, args.baseline_panel)
    canonical_coverage = _resolve(root, args.canonical_coverage)
    canonical_prices = _resolve(root, args.canonical_prices)
    tiingo_cache = _resolve(root, args.tiingo_cache_dir)
    output_dir = _resolve(root, args.output_dir)

    candidate_coverage = candidate_dir / "price_coverage.csv"
    candidate_prices = candidate_dir / "daily_prices.csv.gz"
    candidate_summary = candidate_dir / "summary.json"
    candidate_quality = candidate_dir / "market_price_quality_issues.csv"
    issue7_weekly_summary = issue7_weekly_dir / "summary.json"
    issue7_weekly_panel = issue7_weekly_dir / "candidate_weekly_panel.csv"
    issue7_ttm_summary = issue7_ttm_dir / "summary.json"

    required = {
        "PITIndex data": pitindex,
        "candidate coverage": candidate_coverage,
        "candidate prices": candidate_prices,
        "candidate summary": candidate_summary,
        "candidate quality": candidate_quality,
        "Issue #7 weekly summary": issue7_weekly_summary,
        "Issue #7 weekly panel": issue7_weekly_panel,
        "Issue #7 TTM summary": issue7_ttm_summary,
        "historical baseline panel": baseline_panel,
        "canonical coverage": canonical_coverage,
        "canonical prices": canonical_prices,
        "Tiingo cache": tiingo_cache,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing Issue #19 promotion input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #19 promotion requires a clean tracked source worktree"
        )
    if output_dir.exists():
        raise SystemExit(
            f"Promotion output already exists; preserve it: {output_dir}"
        )

    print("ISSUE #19 CANONICAL MARKET-DATA PROMOTION")
    print("Preflight: validating sealed Issue #7 candidate...", flush=True)
    evidence = _validate_candidate(
        candidate_coverage=candidate_coverage,
        candidate_prices=candidate_prices,
        candidate_summary=candidate_summary,
        candidate_quality=candidate_quality,
        weekly_summary_path=issue7_weekly_summary,
        ttm_summary_path=issue7_ttm_summary,
    )

    output_dir.mkdir(parents=True)
    rollback_dir = output_dir / "rollback_pre_issue19"
    rollback_dir.mkdir()
    rollback_coverage = rollback_dir / "price_coverage.csv"
    rollback_prices = rollback_dir / "daily_prices.csv.gz"

    pre_fingerprints = fingerprint_files(
        root=root,
        paths=[canonical_coverage, canonical_prices],
    )
    candidate_fingerprints = fingerprint_files(
        root=root,
        paths=[candidate_coverage, candidate_prices],
    )
    _copy_verified(canonical_coverage, rollback_coverage)
    _copy_verified(canonical_prices, rollback_prices)
    rollback_fingerprints = fingerprint_files(
        root=root,
        paths=[rollback_coverage, rollback_prices],
    )

    state = {
        "schema_version": 1,
        "status": "PROMOTION_IN_PROGRESS",
        "code": provenance,
        "pre_promotion": pre_fingerprints,
        "candidate": candidate_fingerprints,
        "rollback": rollback_fingerprints,
        "candidate_evidence": evidence,
        "production_live_authorized": False,
        "broker_capabilities": False,
    }
    (output_dir / "promotion_state.json").write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    promoted = False
    try:
        print("Promoting candidate bytes to canonical paths...", flush=True)
        _atomic_replace_from(candidate_coverage, canonical_coverage)
        _atomic_replace_from(candidate_prices, canonical_prices)
        promoted = True

        post_fingerprints = fingerprint_files(
            root=root,
            paths=[canonical_coverage, canonical_prices],
        )
        _require_equal(
            sha256_file(canonical_coverage),
            sha256_file(candidate_coverage),
            label="promoted coverage SHA256",
        )
        _require_equal(
            sha256_file(canonical_prices),
            sha256_file(candidate_prices),
            label="promoted prices SHA256",
        )

        validation = _run(
            [
                python,
                "scripts/validate_canonical_market_data.py",
                "--pitindex-data", str(pitindex),
                "--coverage", str(canonical_coverage),
                "--prices", str(canonical_prices),
                "--start-year", "2015",
                "--end-year", "2025",
            ],
            root=root,
            log_path=output_dir / "post_promotion_validation.txt",
        )
        if validation.returncode != 0:
            raise RuntimeError("Post-promotion canonical validation failed")
        if "RESULT: PASS" not in validation.stdout:
            raise RuntimeError(
                "Post-promotion canonical validator did not report PASS"
            )

        quality_path = output_dir / "post_promotion_quality_issues.csv"
        quality = _run(
            [
                python,
                "scripts/audit_market_price_quality.py",
                "--prices", str(canonical_prices),
                "--coverage", str(canonical_coverage),
                "--tiingo-cache-dir", str(tiingo_cache),
                "--output", str(quality_path),
            ],
            root=root,
            log_path=output_dir / "post_promotion_quality.txt",
        )
        if quality.returncode != 0:
            raise RuntimeError("Post-promotion price-quality audit failed")
        _require_equal(
            _quality_signature(quality_path),
            _quality_signature(candidate_quality),
            label="post-promotion quality disposition",
        )

        weekly_dir = output_dir / "weekly_panel_verification"
        weekly = _run(
            [
                python,
                "scripts/audit_v2_weekly_panel_sensitivity.py",
                "--baseline-panel", str(baseline_panel),
                "--candidate-prices", str(canonical_prices),
                "--output-dir", str(weekly_dir),
            ],
            root=root,
            log_path=output_dir / "weekly_panel_verification.txt",
        )
        if weekly.returncode != 0:
            raise RuntimeError("Post-promotion weekly-panel verification failed")

        weekly_summary = _load_json(weekly_dir / "summary.json")
        _require_equal(
            int(weekly_summary["price_available_gained_rows"]),
            EXPECTED["weekly_price_gain"],
            label="post-promotion weekly price gain",
        )
        _require_equal(
            int(weekly_summary["research_ready_gained_rows"]),
            EXPECTED["weekly_research_ready_gain"],
            label="post-promotion research-ready gain",
        )
        _require_equal(
            int(weekly_summary["unexpected_existing_price_changes"]),
            0,
            label="post-promotion unexpected existing prices",
        )
        _require_equal(
            int(weekly_summary["v1_top10_changed_weeks"]),
            0,
            label="post-promotion V1 Top-10 membership changes",
        )
        _require_equal(
            sha256_file(weekly_dir / "candidate_weekly_panel.csv"),
            sha256_file(issue7_weekly_panel),
            label="post-promotion weekly panel hash",
        )

        ttm_dir = output_dir / "ttm_model_verification"
        ttm = _run(
            [
                python,
                "scripts/audit_v2_ttm_market_sensitivity.py",
                "--as-of", args.as_of,
                "--candidate-panel",
                str(weekly_dir / "candidate_weekly_panel.csv"),
                "--candidate-prices", str(canonical_prices),
                "--output-dir", str(ttm_dir),
            ],
            root=root,
            log_path=output_dir / "ttm_model_verification.txt",
        )
        if ttm.returncode != 0:
            raise RuntimeError("Post-promotion TTM model sensitivity failed")

        issue7_ttm = _load_json(issue7_ttm_summary)
        post_ttm = _load_json(ttm_dir / "summary.json")
        _compare_ttm_results(issue7_ttm, post_ttm)

        tests = _run(
            [python, "-m", "pytest", "-q"],
            root=root,
            log_path=output_dir / "pytest.txt",
        )
        if tests.returncode != 0:
            raise RuntimeError("Post-promotion regression suite failed")

        coverage_counts = _coverage_counts(canonical_coverage)
        price_counts = _price_counts(canonical_prices)
        for key in (
            "coverage_rows",
            "tiingo_selected",
            "unresolved_tickers",
        ):
            _require_equal(
                coverage_counts[key],
                EXPECTED[key],
                label=f"post-promotion {key}",
            )
        for key in ("price_rows", "price_tickers"):
            _require_equal(
                price_counts[key],
                EXPECTED[key],
                label=f"post-promotion {key}",
            )

        state.update({
            "status": "PROMOTION_COMPLETE",
            "post_promotion": post_fingerprints,
            "post_coverage_counts": coverage_counts,
            "post_price_counts": price_counts,
            "post_weekly_summary": weekly_summary,
            "post_ttm_summary": post_ttm,
            "pytest_output": tests.stdout.strip(),
            "rollback_available": True,
            "canonical_equals_candidate": True,
        })
        (output_dir / "promotion_state.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    except Exception as exc:
        if promoted:
            print(
                f"Promotion verification failed: {exc}\n"
                "Restoring pre-Issue-#19 canonical baseline...",
                flush=True,
            )
            _restore(
                rollback_coverage=rollback_coverage,
                rollback_prices=rollback_prices,
                canonical_coverage=canonical_coverage,
                canonical_prices=canonical_prices,
            )
        state.update({
            "status": "PROMOTION_FAILED_ROLLED_BACK" if promoted else "PROMOTION_FAILED",
            "failure": str(exc),
            "rollback_restored": bool(promoted),
        })
        (output_dir / "promotion_state.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise

    print()
    print("ISSUE #19 CANONICAL MARKET-DATA PROMOTION COMPLETE")
    print(
        f"Canonical coverage:           "
        f"{coverage_counts['tiingo_selected']} Tiingo / "
        f"{coverage_counts['unresolved_tickers']} unresolved"
    )
    print(
        f"Canonical daily price rows:   "
        f"{price_counts['price_rows']:,}"
    )
    print(
        f"Weekly price gain reproduced: "
        f"+{weekly_summary['price_available_gained_rows']:,}"
    )
    print(
        f"Research-ready gain reproduced: "
        f"+{weekly_summary['research_ready_gained_rows']:,}"
    )
    print(
        f"V1 / V2 Top-10 changes:       "
        f"{post_ttm['v1_selection_changed_weeks']} / "
        f"{post_ttm['v2_selection_changed_weeks']}"
    )
    print(
        f"V1 / V2 XIRR:                 "
        f"{post_ttm['candidate_v1_xirr']:.2%} / "
        f"{post_ttm['candidate_v2_xirr']:.2%}"
    )
    print(f"Regression suite:             {tests.stdout.strip()}")
    print(f"Rollback baseline:            {rollback_dir}")
    print(f"Promotion manifest:           {output_dir / 'promotion_state.json'}")
    print("NO MODEL, ALLOCATION, LIVE, BROKER, OR ORDER RULE WAS CHANGED.")


if __name__ == "__main__":
    main()
