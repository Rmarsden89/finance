from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from finance.research.v2 import (
    LONG_GROWTH_V2_RESEARCH,
    write_v2_research_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize an isolated long_growth_v2_research run. "
            "This command cannot access a broker or create/review/place orders."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--data-baseline-id", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = write_v2_research_manifest(
        repo_root=args.repo_root,
        as_of=args.as_of,
        data_baseline_id=args.data_baseline_id,
    )

    print("V2 RESEARCH RUN INITIALIZED")
    print(f"Model:        {LONG_GROWTH_V2_RESEARCH.model_id}")
    print(f"As of:        {args.as_of.isoformat()}")
    print(f"Baseline:     {args.data_baseline_id}")
    print(f"Config hash:  {LONG_GROWTH_V2_RESEARCH.configuration_hash}")
    print(f"Manifest:     {output}")
    print()
    print("RESEARCH ONLY: broker access and all order capabilities are disabled.")


if __name__ == "__main__":
    main()
