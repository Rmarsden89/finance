from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


MOMENTUM_FACTORS = (
    "momentum_12m_ex_1m",
    "momentum_6m_ex_1m",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Momentum V1 coverage and provider distributions."
    )
    parser.add_argument("--factors", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/momentum_validation_v1"),
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

    for factor in MOMENTUM_FACTORS:
        validated_col = f"{factor}_validated"
        if validated_col not in frame.columns:
            raise ValueError(f"Missing validated momentum factor: {validated_col}")

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

    continuity = pd.DataFrame([{
        "rows": len(frame),
        "source_change_rows": int(
            frame.get(
                "momentum_source_change",
                pd.Series(False, index=frame.index),
            ).fillna(False).astype(bool).sum()
        ),
        "basis_change_rows": int(
            frame.get(
                "momentum_basis_change",
                pd.Series(False, index=frame.index),
            ).fillna(False).astype(bool).sum()
        ),
        "valid_12m_lookbacks": int(
            frame.get(
                "momentum_12m_lookback_valid",
                pd.Series(False, index=frame.index),
            ).fillna(False).astype(bool).sum()
        ),
        "valid_6m_lookbacks": int(
            frame.get(
                "momentum_6m_lookback_valid",
                pd.Series(False, index=frame.index),
            ).fillna(False).astype(bool).sum()
        ),
    }])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(
        args.output_dir / "momentum_by_price_source.csv",
        index=False,
    )
    pd.DataFrame(yearly_rows).to_csv(
        args.output_dir / "momentum_by_price_source_year.csv",
        index=False,
    )
    continuity.to_csv(
        args.output_dir / "momentum_continuity_summary.csv",
        index=False,
    )

    print("MOMENTUM PROVIDER AUDIT V1")
    print(f"Rows: {len(frame):,}")
    print(f"Reports: {args.output_dir}")


if __name__ == "__main__":
    main()
