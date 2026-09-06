from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from finance.factors.registry import FACTOR_REGISTRY


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit raw V1 factor coverage and distributions."
    )
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/factor_validation_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = pd.read_csv(args.factors, low_memory=False)
    frame["decision_date"] = pd.to_datetime(
        frame["decision_date"],
        errors="coerce",
    )
    frame["year"] = frame["decision_date"].dt.year

    factor_names = list(FACTOR_REGISTRY)
    missing = [name for name in factor_names if name not in frame.columns]
    if missing:
        raise ValueError(
            "Raw factor file missing registered factors: "
            + ", ".join(sorted(missing))
        )

    summaries = []
    yearly = []
    extreme_rows = []

    for name in factor_names:
        values = pd.to_numeric(frame[name], errors="coerce")
        finite = values[np.isfinite(values)]
        available = int(values.notna().sum())
        nonfinite = int((values.notna() & ~np.isfinite(values)).sum())

        quantiles = (
            finite.quantile([0.01, 0.05, 0.50, 0.95, 0.99])
            if not finite.empty
            else pd.Series(dtype=float)
        )

        summaries.append(
            {
                "factor": name,
                "family": FACTOR_REGISTRY[name].family,
                "direction": FACTOR_REGISTRY[name].direction,
                "rows": len(frame),
                "available": available,
                "coverage_pct": (
                    available / len(frame)
                    if len(frame)
                    else 0.0
                ),
                "nonfinite": nonfinite,
                "min": finite.min() if not finite.empty else np.nan,
                "p01": quantiles.get(0.01, np.nan),
                "p05": quantiles.get(0.05, np.nan),
                "median": quantiles.get(0.50, np.nan),
                "p95": quantiles.get(0.95, np.nan),
                "p99": quantiles.get(0.99, np.nan),
                "max": finite.max() if not finite.empty else np.nan,
            }
        )

        for year, group in frame.groupby("year", dropna=True):
            year_values = pd.to_numeric(group[name], errors="coerce")
            year_finite = year_values[np.isfinite(year_values)]
            yearly.append(
                {
                    "year": int(year),
                    "factor": name,
                    "family": FACTOR_REGISTRY[name].family,
                    "rows": len(group),
                    "available": int(year_values.notna().sum()),
                    "coverage_pct": (
                        year_values.notna().mean()
                        if len(group)
                        else 0.0
                    ),
                    "median": (
                        year_finite.median()
                        if not year_finite.empty
                        else np.nan
                    ),
                    "p05": (
                        year_finite.quantile(0.05)
                        if not year_finite.empty
                        else np.nan
                    ),
                    "p95": (
                        year_finite.quantile(0.95)
                        if not year_finite.empty
                        else np.nan
                    ),
                }
            )

        if not finite.empty:
            lower = finite.quantile(0.001)
            upper = finite.quantile(0.999)
            mask = values.notna() & ((values < lower) | (values > upper))
            for row in frame.loc[
                mask,
                ["decision_date", "ticker", name],
            ].head(100).itertuples(index=False):
                extreme_rows.append(
                    {
                        "factor": name,
                        "decision_date": row[0],
                        "ticker": row[1],
                        "value": row[2],
                        "p001": lower,
                        "p999": upper,
                    }
                )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_frame = pd.DataFrame(summaries)
    yearly_frame = pd.DataFrame(yearly)
    extremes_frame = pd.DataFrame(extreme_rows)

    summary_frame.to_csv(
        args.output_dir / "factor_summary.csv",
        index=False,
    )
    yearly_frame.to_csv(
        args.output_dir / "factor_yearly.csv",
        index=False,
    )
    extremes_frame.to_csv(
        args.output_dir / "factor_extremes.csv",
        index=False,
    )

    print("RAW FACTOR AUDIT V1")
    print(f"Rows:       {len(frame):,}")
    print(f"Factors:    {len(factor_names)}")
    print()
    for row in summary_frame.itertuples(index=False):
        print(
            f"{row.factor:38s} "
            f"coverage={row.coverage_pct:7.2%} "
            f"median={row.median:12.4g} "
            f"p05={row.p05:12.4g} "
            f"p95={row.p95:12.4g} "
            f"nonfinite={int(row.nonfinite):4d}"
        )
    print()
    print(f"Reports:    {args.output_dir}")


if __name__ == "__main__":
    main()
