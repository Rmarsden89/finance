from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from finance.research.v2 import (
    resolve_v2_sec_artifact_paths,
    resolve_v2_ttm_diagnostic_paths,
)
from finance.research.v3 import (
    V3_COMBINED_CHALLENGER,
    validate_v3_combined_challenger_freeze,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify deterministic current V2-vs-V3 comparison output and "
            "prove saved V2 inputs are unchanged by the comparison rerun."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path): _sha256(path) for path in paths}


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    as_of = args.as_of.isoformat()

    validate_v3_combined_challenger_freeze()
    if as_of != V3_COMBINED_CHALLENGER.evidence_as_of:
        raise SystemExit(
            "Frozen V3 v1 evidence date is "
            f"{V3_COMBINED_CHALLENGER.evidence_as_of}; got {as_of}"
        )

    v2 = resolve_v2_sec_artifact_paths(root, args.as_of)
    ttm = resolve_v2_ttm_diagnostic_paths(root, args.as_of)
    output_dir = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / as_of
        / "v3_current_v2_comparison"
    )

    comparison_outputs = [
        output_dir / "summary.json",
        output_dir / "v2_v3_current_detail.csv",
        output_dir / "v2_v3_top10_comparison.csv",
        output_dir / "v3_current_scores.csv",
        output_dir / "input_fingerprints.json",
    ]
    v2_inputs = [
        v2["scoring_panel"],
        v2["current_snapshot"],
        ttm["ttm_current_numerators"],
        ttm["ttm_latest_by_concept"],
    ]

    required = comparison_outputs + v2_inputs
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            "Missing determinism input/output(s):\n  " + "\n  ".join(missing)
        )

    output_before = _hashes(comparison_outputs)
    v2_before = _hashes(v2_inputs)

    subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "compare_v2_v3_current.py"),
            "--as-of",
            as_of,
            "--repo-root",
            str(root),
        ],
        cwd=root,
        check=True,
    )

    output_after = _hashes(comparison_outputs)
    v2_after = _hashes(v2_inputs)

    output_differences = sorted(
        path
        for path in output_before
        if output_before[path] != output_after[path]
    )
    v2_differences = sorted(
        path
        for path in v2_before
        if v2_before[path] != v2_after[path]
    )

    result = {
        "schema_version": 1,
        "status": (
            "V3_DETERMINISM_VERIFIED"
            if not output_differences and not v2_differences
            else "V3_DETERMINISM_FAILED"
        ),
        "as_of": as_of,
        "model_id": V3_COMBINED_CHALLENGER.model_id,
        "model_configuration_hash": V3_COMBINED_CHALLENGER.configuration_hash,
        "comparison_outputs_identical": not output_differences,
        "v2_inputs_unchanged": not v2_differences,
        "output_differences": output_differences,
        "v2_input_differences": v2_differences,
        "comparison_output_sha256": output_after,
        "v2_input_sha256": v2_after,
        "execution_capabilities": {
            "broker_access": False,
            "order_intents": False,
            "order_review": False,
            "order_placement": False,
        },
    }

    result_path = output_dir / "determinism_verification.json"
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V3 CURRENT COMPARISON DETERMINISM VERIFICATION")
    print(f"As of:                       {as_of}")
    print(
        "Comparison outputs identical: "
        f"{result['comparison_outputs_identical']}"
    )
    print(
        f"V2 inputs unchanged:         {result['v2_inputs_unchanged']}"
    )
    print(f"Status:                      {result['status']}")
    print(f"Verification:                {result_path}")
    print("NO BROKER OR ORDER CAPABILITY EXISTS IN THIS COMMAND.")

    if output_differences or v2_differences:
        raise SystemExit("V3 determinism verification failed")


if __name__ == "__main__":
    main()
