from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


STABILITY_FACTORS = (
    "volatility_52w",
    "downside_deviation_52w",
    "max_drawdown_52w",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit V1 stability-factor distributions by canonical price source."
        )
    )
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/stability_validation_v1"),
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

    summary_rows = []
    yearly_rows = []

    for factor in STABILITY_FACTORS:
        validated_col = f"{factor}_validated"
        if validated_col not in frame.columns:
            raise ValueError(f"Missing validated stability factor: {validated_col}")

        for source, group in frame.groupby("price_source", dropna=False):
            values = pd.to_numeric(group[validated_col], errors="coerce")
            finite = values[np.isfinite(values)]
            summary_rows.append({
                "factor": factor,
                "price_source": source,
                "rows": len(group),
                "available": int(values.notna().sum()),
                "coverage_pct": values.notna().mean() if len(group) else 0.0,
                "median": finite.median() if not finite.empty else np.nan,
                "p05": finite.quantile(0.05) if not finite.empty else np.nan,
                "p95": finite.quantile(0.95) if not finite.empty else np.nan,
            })

        for (year, source), group in frame.groupby(
            ["year", "price_source"],
            dropna=False,
        ):
            values = pd.to_numeric(group[validated_col], errors="coerce")
            finite = values[np.isfinite(values)]
            yearly_rows.append({
                "year": year,
                "factor": factor,
                "price_source": source,
                "rows": len(group),
                "available": int(values.notna().sum()),
                "coverage_pct": values.notna().mean() if len(group) else 0.0,
                "median": finite.median() if not finite.empty else np.nan,
                "p05": finite.quantile(0.05) if not finite.empty else np.nan,
                "p95": finite.quantile(0.95) if not finite.empty else np.nan,
            })

    source_change_rows = int(
        frame.get(
            "stability_source_change",
            pd.Series(False, index=frame.index),
        ).fillna(False).astype(bool).sum()
    )
    basis_change_rows = int(
        frame.get(
            "stability_basis_change",
            pd.Series(False, index=frame.index),
        ).fillna(False).astype(bool).sum()
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(
        args.output_dir / "stability_by_price_source.csv",
        index=False,
    )
    pd.DataFrame(yearly_rows).to_csv(
        args.output_dir / "stability_by_price_source_year.csv",
        index=False,
    )
    pd.DataFrame([{
        "rows": len(frame),
        "source_change_rows": source_change_rows,
        "basis_change_rows": basis_change_rows,
    }]).to_csv(
        args.output_dir / "stability_continuity_summary.csv",
        index=False,
    )

    print("STABILITY PROVIDER AUDIT V1")
    print(f"Rows:                 {len(frame):,}")
    print(f"Source-change resets: {source_change_rows:,}")
    print(f"Basis-change resets:  {basis_change_rows:,}")
    print(f"Reports:              {args.output_dir}")


if __name__ == "__main__":
    main()
