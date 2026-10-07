from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd

from finance.research.v4 import (
    V4_DECK_LIABILITIES_RULE,
    validate_v4_deck_liabilities_freeze,
)


PROTECTED_FILES = (
    "src/finance/models/long_growth_v1.py",
    "src/finance/research/v2.py",
    "src/finance/research/v3.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run fail-closed V4 closeout checks for the frozen DECK data fix. "
            "No model inputs or broker state are modified."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref", default="origin/main")
    return parser.parse_args()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout


def _assert_equal(name: str, actual, expected) -> None:
    if actual != expected:
        raise SystemExit(f"{name} failed: {actual!r} != {expected!r}")


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    checks: list[dict[str, object]] = []

    # 1. Frozen V4 contract
    validate_v4_deck_liabilities_freeze()
    rule = V4_DECK_LIABILITIES_RULE
    _assert_equal("ticker", rule.ticker, "DECK")
    _assert_equal("cik", rule.cik, 910521)
    _assert_equal("first_supported_period", rule.first_supported_period, "2015-03-31")
    _assert_equal(
        "last_material_validation_period",
        rule.last_material_validation_period,
        "2014-12-31",
    )
    _assert_equal("overwrite_positive_canonical_liabilities", rule.overwrite_positive_canonical_liabilities, False)
    _assert_equal("assets_minus_equity_recovery_allowed", rule.assets_minus_equity_recovery_allowed, False)
    _assert_equal("total_like_minus_equity_recovery_allowed", rule.total_like_minus_equity_recovery_allowed, False)
    _assert_equal("broker_access_enabled", rule.broker_access_enabled, False)
    _assert_equal("order_placement_enabled", rule.order_placement_enabled, False)
    checks.append({"check": "frozen_v4_contract", "status": "PASS"})

    # 2. Historical evaluation summary
    regime_dir = (
        root / "reports" / "v4" / "data_sources" / as_of
        / "deck_post_material_regime"
    )
    summary_path = regime_dir / "summary.json"
    recovery_path = regime_dir / "recovery_detail.csv"
    if not summary_path.exists() or not recovery_path.exists():
        raise SystemExit("Missing DECK regime evaluation artifacts")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    required_summary = {
        "status": "V4_DECK_REGIME_EVALUATION_COMPLETE",
        "ticker": "DECK",
        "last_material_period": "2014-12-31",
        "first_clean_period_after_material": "2015-03-31",
        "post_material_material_rows": 0,
        "post_material_ambiguous_rows": 0,
        "post_material_regime_clean": True,
        "recovery_rows_applied": 93,
        "pit_violations": 0,
        "research_only": True,
        "v1_modified": False,
        "v2_modified": False,
        "v3_modified": False,
    }
    for key, expected in required_summary.items():
        _assert_equal(f"summary.{key}", summary.get(key), expected)
    checks.append({"check": "historical_evaluation_summary", "status": "PASS"})

    # 3. Recovery scope and overwrite containment
    recovery = pd.read_csv(recovery_path, low_memory=False)
    if len(recovery) != 93:
        raise SystemExit(f"Recovery row count drift: {len(recovery)} != 93")
    tickers = set(recovery["ticker"].astype(str).str.upper().str.strip())
    _assert_equal("recovery ticker set", tickers, {"DECK"})

    period = pd.to_datetime(recovery["selected_period_date"], errors="coerce")
    if period.isna().any():
        raise SystemExit("Recovery artifact contains unparseable selected_period_date")
    if period.min() < pd.Timestamp("2015-03-31"):
        raise SystemExit(
            f"Recovery before frozen regime start: {period.min().date()}"
        )

    baseline = pd.to_numeric(
        recovery["baseline_total_liabilities"], errors="coerce"
    )
    overwritten = baseline.gt(0)
    if overwritten.any():
        raise SystemExit(
            f"Positive canonical liabilities overwritten in {int(overwritten.sum())} rows"
        )

    constructed = pd.to_numeric(
        recovery["constructed_liabilities"], errors="coerce"
    )
    final = pd.to_numeric(recovery["total_liabilities"], errors="coerce")
    if constructed.isna().any() or ~constructed.gt(0).all():
        raise SystemExit("Recovery artifact contains invalid constructed liabilities")
    if not final.equals(constructed):
        mismatch = (final != constructed).fillna(True)
        raise SystemExit(
            f"Recovered liabilities differ from construction in {int(mismatch.sum())} rows"
        )

    if "v4_deck_regime_rule_applied" in recovery.columns:
        applied = (
            recovery["v4_deck_regime_rule_applied"]
            .fillna(False)
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
        if not applied.all():
            raise SystemExit("Recovery artifact contains rows without V4 rule applied")

    checks.append({"check": "scope_and_no_overwrite", "status": "PASS"})

    # 4. V1/V2/V3 protected source files unchanged relative to base.
    #
    # Compare Git blob identities and Git's normalized working-tree diff rather
    # than raw filesystem bytes. On Windows, core.autocrlf may materialize CRLF
    # locally while the repository blob remains LF, which is not model drift.
    protected_hashes: dict[str, dict[str, str]] = {}
    for rel in PROTECTED_FILES:
        current_path = root / rel
        if not current_path.exists():
            raise SystemExit(f"Missing protected file: {rel}")

        base_blob = _git(root, "rev-parse", f"{args.base_ref}:{rel}").strip()
        head_blob = _git(root, "rev-parse", f"HEAD:{rel}").strip()
        if head_blob != base_blob:
            raise SystemExit(
                f"Frozen model/rule file committed on branch differs from "
                f"{args.base_ref}: {rel}"
            )

        diff = subprocess.run(
            ["git", "diff", "--quiet", "--", rel],
            cwd=root,
            check=False,
        )
        if diff.returncode not in {0, 1}:
            raise SystemExit(f"git diff check failed for protected file: {rel}")
        if diff.returncode == 1:
            raise SystemExit(
                f"Frozen model/rule file has local working-tree changes: {rel}"
            )

        protected_hashes[rel] = {
            "base_blob_sha": base_blob,
            "head_blob_sha": head_blob,
        }

    checks.append({"check": "v1_v2_v3_protected_files_unchanged", "status": "PASS"})

    # 5. Deterministic fingerprint of the frozen closeout evidence
    fingerprint_inputs = {
        "rule_configuration_hash": rule.configuration_hash,
        "summary_sha256": _sha256_file(summary_path),
        "recovery_detail_sha256": _sha256_file(recovery_path),
        "protected_files": protected_hashes,
    }
    canonical = json.dumps(
        fingerprint_inputs,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    closeout_fingerprint = _sha256_bytes(canonical)

    output_dir = (
        root / "reports" / "v4" / "data_sources" / as_of / "closeout"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    prior_path = output_dir / "closeout_fingerprint.json"

    prior_match = None
    if prior_path.exists():
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        prior_value = prior.get("closeout_fingerprint")
        prior_match = prior_value == closeout_fingerprint
        if not prior_match:
            raise SystemExit(
                "Determinism check failed: closeout fingerprint changed from prior run"
            )

    payload = {
        "schema_version": 1,
        "status": "V4_CLOSEOUT_CHECKS_PASS",
        "as_of": as_of,
        "rule_id": rule.rule_id,
        "rule_configuration_hash": rule.configuration_hash,
        "closeout_fingerprint": closeout_fingerprint,
        "prior_fingerprint_match": prior_match,
        "checks": checks,
        "fingerprint_inputs": fingerprint_inputs,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }
    prior_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V4 DATA-FIX CLOSEOUT CHECKS")
    print(f"As of:                       {as_of}")
    print(f"Rule:                        {rule.rule_id}")
    print(f"Rule hash:                   {rule.configuration_hash}")
    print(f"Recovery rows:               {len(recovery)}")
    print(f"Recovery ticker set:         {','.join(sorted(tickers))}")
    print(f"Earliest recovery period:    {period.min().date().isoformat()}")
    print(f"Positive overwrites:         {int(overwritten.sum())}")
    print(f"PIT violations:              {summary.get('pit_violations')}")
    print(f"Protected files checked:     {len(PROTECTED_FILES)}")
    print(f"Closeout fingerprint:        {closeout_fingerprint}")
    print(
        "Prior fingerprint match:    "
        + (
            "FIRST_RUN"
            if prior_match is None
            else str(prior_match)
        )
    )
    print()
    for item in checks:
        print(f"PASS  {item['check']}")
    print()
    print(f"Output:                      {prior_path}")
    print("ALL V4 CLOSEOUT CHECKS PASSED.")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")


if __name__ == "__main__":
    main()
