from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review Issue #7 V1 weekly Top-10 sensitivity details."
    )
    parser.add_argument(
        "--comparison",
        type=Path,
        default=Path(
            "reports/v2/long_growth_v2_research/issue7_market_data/"
            "weekly_panel_sensitivity_v1/v1_top10_weekly_comparison.csv"
        ),
    )
    return parser.parse_args()


def _count(value: object) -> int:
    if value is None or pd.isna(value):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    return len([item for item in text.split("|") if item])


def main() -> None:
    args = parse_args()
    if not args.comparison.exists():
        raise SystemExit(f"Missing comparison: {args.comparison}")

    frame = pd.read_csv(args.comparison, low_memory=False)
    frame["before_count"] = frame["before_top10"].map(_count)
    frame["after_count"] = frame["after_top10"].map(_count)
    frame["full_before"] = frame["before_count"].eq(10)
    frame["full_after"] = frame["after_count"].eq(10)
    frame["full_both"] = frame["full_before"] & frame["full_after"]

    changed = frame.loc[frame["changed"].fillna(False).astype(bool)].copy()
    full = frame.loc[frame["full_both"]].copy()

    print("ISSUE #7 V1 TOP-10 CHANGE REVIEW")
    print(f"Decision weeks:               {len(frame):,}")
    print(f"Full Top-10 before:           {int(frame['full_before'].sum()):,}")
    print(f"Full Top-10 after:            {int(frame['full_after'].sum()):,}")
    print(f"Full Top-10 both:             {len(full):,}")
    print(f"Changed weeks:                {len(changed):,}")
    if not full.empty:
        print(
            f"Full-week min / mean overlap: "
            f"{int(full['top10_overlap'].min())}/10 / "
            f"{full['top10_overlap'].mean():.3f}/10"
        )
    print()
    print("CHANGED WEEKS")
    if changed.empty:
        print("none")
    else:
        for row in changed.itertuples(index=False):
            print(
                f"{row.decision_date} "
                f"count={row.before_count}->{row.after_count} "
                f"overlap={int(row.top10_overlap)} "
                f"entered={row.entered or '-'} "
                f"exited={row.exited or '-'}"
            )


if __name__ == "__main__":
    main()
