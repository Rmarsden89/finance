from __future__ import annotations

import argparse
import json
from pathlib import Path

from finance.evaluation.package import EvaluationPackageError, build_v1_evaluation_package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=("Build the read-only V1 evaluation package for one completed live run. This command has no broker, review, preview, or order capability."))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or (args.run_dir / "evaluation_package.json")
    try:
        pkg = build_v1_evaluation_package(args.run_dir)
    except EvaluationPackageError as exc:
        raise SystemExit(f"V1 evaluation package failed closed: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(pkg, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("V1 EVALUATION PACKAGE")
    print("Evaluation only:          YES")
    print("Broker/order capability:  NONE")
    print(f"Run date:                 {pkg['run_date']}")
    print(f"Decision hash:            {pkg['decision_hash']}")
    print(f"Deployed contribution:    ${pkg['deployed_contribution']:.2f}")
    print(f"Post-fill position value: ${pkg['postfill_position_value']:.2f}")
    print(f"Selections:               {pkg['selection_count']}")
    print(f"SPY capture:              {pkg['benchmark']['status']}")
    print(f"Output:                   {output}")


if __name__ == "__main__":
    main()
