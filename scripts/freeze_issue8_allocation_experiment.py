from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance.research.allocation_challengers import experiment_manifest
from finance.research.fingerprints import fingerprint_files, git_provenance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze Issue #8 allocation formulas, parameters, and ranked-input "
            "lineage before any allocation performance comparison is run."
        )
    )
    parser.add_argument(
        "--issue19-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/"
            "issue19_market_promotion/promotion_v1"
        ),
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
        "--output-dir",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/"
            "issue8_allocation_challengers/frozen_v1"
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _resolve(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def _fingerprint_sha(bundle: dict[str, object], suffix: str) -> str:
    for row in bundle.get("files", []):
        if str(row.get("path", "")).endswith(suffix):
            return str(row["sha256"])
    raise SystemExit(f"Fingerprint bundle lacks {suffix}")


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    issue19 = _resolve(root, args.issue19_dir)
    canonical_coverage = _resolve(root, args.canonical_coverage)
    canonical_prices = _resolve(root, args.canonical_prices)
    output_dir = _resolve(root, args.output_dir)

    promotion_state_path = issue19 / "promotion_state.json"
    ranked_v1 = issue19 / "ttm_model_verification" / "candidate_v1_panel.csv"
    ranked_v2 = issue19 / "ttm_model_verification" / "candidate_v2_panel.csv"

    required = {
        "Issue #19 promotion state": promotion_state_path,
        "promoted canonical coverage": canonical_coverage,
        "promoted canonical prices": canonical_prices,
        "frozen promoted V1 ranked panel": ranked_v1,
        "frozen promoted V2 ranked panel": ranked_v2,
    }
    missing = [
        f"{name}: {path}"
        for name, path in required.items()
        if not path.exists()
    ]
    if missing:
        raise SystemExit(
            "Missing Issue #8 freeze input(s):\n  "
            + "\n  ".join(missing)
        )

    provenance = git_provenance(root)
    if not provenance["tracked_worktree_clean"]:
        raise SystemExit(
            "Issue #8 experiment freeze requires a clean tracked worktree"
        )
    if output_dir.exists():
        raise SystemExit(
            f"Frozen Issue #8 experiment already exists: {output_dir}"
        )

    promotion = json.loads(
        promotion_state_path.read_text(encoding="utf-8")
    )
    if promotion.get("status") != "PROMOTION_COMPLETE":
        raise SystemExit("Issue #19 promotion is not complete")
    if not bool(promotion.get("canonical_equals_candidate")):
        raise SystemExit(
            "Issue #19 promotion does not certify canonical=candidate"
        )

    canonical = fingerprint_files(
        root=root,
        paths=[canonical_coverage, canonical_prices],
    )
    issue19_post = promotion.get("post_promotion", {})
    for suffix in ("price_coverage.csv", "daily_prices.csv.gz"):
        expected = _fingerprint_sha(issue19_post, suffix)
        actual = _fingerprint_sha(canonical, suffix)
        if expected != actual:
            raise SystemExit(
                f"Canonical baseline drift since Issue #19 for {suffix}: "
                f"{actual} != {expected}"
            )

    ranked_inputs = fingerprint_files(
        root=root,
        paths=[ranked_v1, ranked_v2],
    )
    manifest = experiment_manifest()
    manifest.update({
        "status": "ISSUE8_EXPERIMENT_FROZEN",
        "canonical_market_baseline": canonical,
        "ranked_inputs": ranked_inputs,
        "ranked_input_paths": {
            "v1": str(ranked_v1.relative_to(root)),
            "v2": str(ranked_v2.relative_to(root)),
        },
        "issue19_promotion_state": (
            str(promotion_state_path.relative_to(root))
        ),
        "code": provenance,
        "results_reviewed_before_freeze": False,
        "live_rule_changed": False,
    })

    output_dir.mkdir(parents=True)
    (output_dir / "experiment_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("ISSUE #8 ALLOCATION EXPERIMENT FROZEN")
    print("Experiment ID:                allocation_challengers_v1")
    print("Weekly contribution:          $10.00")
    print("Top N:                        10")
    print("Max-addon position gate:      10%")
    print("Discretionary selling:        disabled")
    print("Allocation rules:")
    print("  equal_dollar")
    print("  rank_weighted")
    print("  score_weighted")
    print("  conviction_bands")
    print("Rolling windows:              3y, 5y")
    print("Leave-winner-out count:       1")
    print("Score sensitivity multiples:  0.50, 0.75, 1.00, 1.25, 1.50")
    print("Rank perturbation swaps:      1/2, 3/4, 5/6, 9/10")
    print(f"Frozen V1 panel:              {ranked_v1}")
    print(f"Frozen V2 panel:              {ranked_v2}")
    print(f"Manifest:                     {output_dir / 'experiment_manifest.json'}")
    print("NO ALLOCATION PERFORMANCE RESULT WAS GENERATED.")
    print("NO LIVE, BROKER, OR ORDER RULE WAS CHANGED.")


if __name__ == "__main__":
    main()
