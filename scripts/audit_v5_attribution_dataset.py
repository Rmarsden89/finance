from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the V5 historical attribution dataset contract."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("reports/v5/attribution_dataset/v5_historical_attribution_dataset.csv"),
    )
    parser.add_argument(
        "--horizons-weeks",
        default="1,4,13,26,52",
    )
    parser.add_argument("--max-exit-delay-days", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/v5/attribution_dataset/audit.json"),
    )
    return parser.parse_args()


def _bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def _date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def audit_dataset(
    frame: pd.DataFrame,
    *,
    horizons_weeks: tuple[int, ...],
    max_exit_delay_days: int,
) -> dict:
    required = {
        "decision_date",
        "ticker",
        "long_growth_v1_score",
        "top_conviction_eligible",
        "v1_rank",
        "v1_top10",
        "v1_top25",
        "v1_rank_band",
        "forward_entry_price",
        "forward_entry_price_date",
        "spy_entry_price",
        "spy_entry_price_date",
    }
    for weeks in horizons_weeks:
        prefix = f"fwd_{weeks}w"
        required.update({
            f"{prefix}_return",
            f"{prefix}_exit_date",
            f"{prefix}_spy_return",
            f"{prefix}_spy_exit_date",
            f"{prefix}_excess_return",
            f"{prefix}_status",
        })
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("Dataset missing required columns: " + ", ".join(missing))

    data = frame.copy()
    data["decision_date"] = _date(data["decision_date"])
    if data["decision_date"].isna().any():
        raise ValueError("Invalid decision_date values")
    data["ticker"] = data["ticker"].astype(str).str.strip().str.upper()
    duplicate_keys = int(data[["decision_date", "ticker"]].duplicated().sum())

    ranked = pd.to_numeric(data["v1_rank"], errors="coerce")
    eligible = _bool(data["top_conviction_eligible"])
    score = pd.to_numeric(data["long_growth_v1_score"], errors="coerce")
    top10 = _bool(data["v1_top10"])
    top25 = _bool(data["v1_top25"])

    rank_on_ineligible = int((ranked.notna() & ~(eligible & score.notna())).sum())
    eligible_missing_rank = int(((eligible & score.notna()) & ranked.isna()).sum())
    top10_rank_mismatch = int((top10 != ranked.between(1, 10).fillna(False)).sum())
    top25_rank_mismatch = int((top25 != ranked.between(1, 25).fillna(False)).sum())

    duplicate_rank_rows = 0
    noncontiguous_rank_dates = 0
    top10_count_failures = 0
    for decision_day, group in data.assign(_rank=ranked).groupby("decision_date", sort=False):
        ranks = group["_rank"].dropna().astype(int)
        duplicate_rank_rows += int(ranks.duplicated().sum())
        if not ranks.empty and sorted(ranks.tolist()) != list(range(1, len(ranks) + 1)):
            noncontiguous_rank_dates += 1
        expected_top10 = min(10, len(ranks))
        if int(_bool(group["v1_top10"]).sum()) != expected_top10:
            top10_count_failures += 1

    entry_date = _date(data["forward_entry_price_date"])
    spy_entry_date = _date(data["spy_entry_price_date"])
    future_entry_dates = int(
        (entry_date.notna() & (entry_date > data["decision_date"])).sum()
    )
    future_spy_entry_dates = int(
        (spy_entry_date.notna() & (spy_entry_date > data["decision_date"])).sum()
    )

    horizon_summary: dict[str, dict[str, int | float]] = {}
    total_contract_failures = 0

    for weeks in horizons_weeks:
        prefix = f"fwd_{weeks}w"
        target = pd.Series(
            [day + timedelta(weeks=weeks) for day in data["decision_date"]],
            index=data.index,
        )
        exit_date = _date(data[f"{prefix}_exit_date"])
        spy_exit_date = _date(data[f"{prefix}_spy_exit_date"])
        stock_return = pd.to_numeric(data[f"{prefix}_return"], errors="coerce")
        spy_return = pd.to_numeric(data[f"{prefix}_spy_return"], errors="coerce")
        excess = pd.to_numeric(data[f"{prefix}_excess_return"], errors="coerce")
        status = data[f"{prefix}_status"].astype(str)

        before_target = int((exit_date.notna() & (exit_date < target)).sum())
        exit_delay_days = pd.Series(np.nan, index=data.index, dtype="float64")
        valid_exit = exit_date.notna()
        exit_delay_days.loc[valid_exit] = [
            (exit_day - target_day).days
            for exit_day, target_day in zip(
                exit_date.loc[valid_exit],
                target.loc[valid_exit],
            )
        ]
        after_window = int(
            (valid_exit & (exit_delay_days > max_exit_delay_days)).sum()
        )

        spy_before_target = int((spy_exit_date.notna() & (spy_exit_date < target)).sum())
        spy_exit_delay_days = pd.Series(np.nan, index=data.index, dtype="float64")
        valid_spy_exit = spy_exit_date.notna()
        spy_exit_delay_days.loc[valid_spy_exit] = [
            (exit_day - target_day).days
            for exit_day, target_day in zip(
                spy_exit_date.loc[valid_spy_exit],
                target.loc[valid_spy_exit],
            )
        ]
        spy_after_window = int(
            (valid_spy_exit & (spy_exit_delay_days > max_exit_delay_days)).sum()
        )

        mature = status.eq("mature")
        mature_missing = int(
            (
                mature
                & (
                    stock_return.isna()
                    | spy_return.isna()
                    | excess.isna()
                    | exit_date.isna()
                    | spy_exit_date.isna()
                )
            ).sum()
        )
        excess_mismatch = int(
            (
                mature
                & ~np.isclose(
                    excess,
                    stock_return - spy_return,
                    rtol=1e-10,
                    atol=1e-12,
                    equal_nan=False,
                )
            ).sum()
        )

        missing_exit_has_return = int(
            (status.eq("missing_exit_price") & stock_return.notna()).sum()
        )
        missing_spy_exit_has_spy_return = int(
            (status.eq("missing_spy_exit") & spy_return.notna()).sum()
        )
        unknown_status = int(
            (
                ~status.isin({
                    "mature",
                    "missing_entry_price",
                    "missing_exit_price",
                    "missing_spy_entry",
                    "missing_spy_exit",
                })
            ).sum()
        )

        failures = (
            before_target
            + after_window
            + spy_before_target
            + spy_after_window
            + mature_missing
            + excess_mismatch
            + missing_exit_has_return
            + missing_spy_exit_has_spy_return
            + unknown_status
        )
        total_contract_failures += failures

        horizon_summary[str(weeks)] = {
            "mature_rows": int(mature.sum()),
            "missing_entry_price_rows": int(status.eq("missing_entry_price").sum()),
            "missing_exit_price_rows": int(status.eq("missing_exit_price").sum()),
            "missing_spy_entry_rows": int(status.eq("missing_spy_entry").sum()),
            "missing_spy_exit_rows": int(status.eq("missing_spy_exit").sum()),
            "pending_rows": int(status.eq("pending").sum()),
            "exit_before_target": before_target,
            "exit_after_window": after_window,
            "spy_exit_before_target": spy_before_target,
            "spy_exit_after_window": spy_after_window,
            "mature_missing_values": mature_missing,
            "excess_return_mismatches": excess_mismatch,
            "missing_exit_with_return": missing_exit_has_return,
            "missing_spy_exit_with_return": missing_spy_exit_has_spy_return,
            "unknown_status_rows": unknown_status,
        }

    base_failures = (
        duplicate_keys
        + rank_on_ineligible
        + eligible_missing_rank
        + top10_rank_mismatch
        + top25_rank_mismatch
        + duplicate_rank_rows
        + noncontiguous_rank_dates
        + top10_count_failures
        + future_entry_dates
        + future_spy_entry_dates
    )
    total_failures = base_failures + total_contract_failures

    return {
        "schema_version": 1,
        "research_only": True,
        "rows": int(len(data)),
        "decision_dates": int(data["decision_date"].nunique()),
        "unique_tickers": int(data["ticker"].nunique()),
        "duplicate_keys": duplicate_keys,
        "rank_on_ineligible_rows": rank_on_ineligible,
        "eligible_missing_rank_rows": eligible_missing_rank,
        "top10_rank_mismatches": top10_rank_mismatch,
        "top25_rank_mismatches": top25_rank_mismatch,
        "duplicate_rank_rows": duplicate_rank_rows,
        "noncontiguous_rank_dates": noncontiguous_rank_dates,
        "top10_count_failures": top10_count_failures,
        "future_entry_dates": future_entry_dates,
        "future_spy_entry_dates": future_spy_entry_dates,
        "horizons": horizon_summary,
        "contract_failures": total_failures,
        "status": "PASS" if total_failures == 0 else "FAIL",
    }


def main() -> None:
    args = parse_args()
    horizons = tuple(
        int(value.strip()) for value in args.horizons_weeks.split(",") if value.strip()
    )
    frame = pd.read_csv(args.dataset, low_memory=False)
    result = audit_dataset(
        frame,
        horizons_weeks=horizons,
        max_exit_delay_days=args.max_exit_delay_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("V5 ATTRIBUTION DATASET AUDIT")
    print(f"Status:                    {result['status']}")
    print(f"Rows:                      {result['rows']:,}")
    print(f"Decision dates:            {result['decision_dates']:,}")
    print(f"Unique tickers:            {result['unique_tickers']:,}")
    print(f"Duplicate keys:            {result['duplicate_keys']}")
    print(f"Contract failures:         {result['contract_failures']}")
    for weeks in horizons:
        item = result["horizons"][str(weeks)]
        print(
            f"{weeks:>2}w mature/pending/missing: "
            f"{item['mature_rows']:,}/"
            f"{item['pending_rows']:,}/"
            f"{item['missing_exit_price_rows']:,}"
        )
        print(
            f"    SPY missing entry/exit: "
            f"{item['missing_spy_entry_rows']:,}/"
            f"{item['missing_spy_exit_rows']:,}"
        )
    print(f"Output:                    {args.output}")

    if result["status"] != "PASS":
        raise SystemExit("V5 attribution dataset audit failed closed.")


if __name__ == "__main__":
    main()
