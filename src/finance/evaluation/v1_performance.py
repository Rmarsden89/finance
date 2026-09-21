from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd


class EvaluationError(RuntimeError):
    pass


HORIZONS = {
    "1w": 7,
    "4w": 28,
    "13w": 91,
    "26w": 182,
    "52w": 364,
}


@dataclass(frozen=True)
class EvaluationResult:
    summary: dict[str, Any]
    weekly_history: pd.DataFrame
    benchmark_history: pd.DataFrame
    selection_cohorts: pd.DataFrame
    selection_forward_returns: pd.DataFrame


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluationError(f"Missing required evaluation artifact: {path}") from exc


def _completed_run_dirs(shadow_root: Path) -> list[Path]:
    if not shadow_root.exists():
        raise EvaluationError(f"Shadow root not found: {shadow_root}")
    runs: list[Path] = []
    for child in shadow_root.iterdir():
        if not child.is_dir():
            continue
        try:
            date.fromisoformat(child.name)
        except ValueError:
            continue
        state_path = child / "workflow_state.json"
        if state_path.exists() and _read_json(state_path).get("status") == "COMPLETE":
            runs.append(child)
    runs.sort(key=lambda path: path.name)
    if not runs:
        raise EvaluationError(f"No COMPLETE V1 live runs found under {shadow_root}")
    return runs


def _load_benchmark_prices(path: Path, benchmark: str) -> pd.DataFrame:
    if not path.exists():
        raise EvaluationError(f"Benchmark price file not found: {path}")
    frame = pd.read_csv(path)
    missing = sorted({"ticker", "date"} - set(frame.columns))
    if missing:
        raise EvaluationError("Benchmark price file missing columns: " + ", ".join(missing))
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame = frame.loc[frame["ticker"] == benchmark.upper()].copy()
    if frame.empty:
        raise EvaluationError(f"No {benchmark.upper()} rows in benchmark price file")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    adjusted = (
        pd.to_numeric(frame["adjusted_close"], errors="coerce")
        if "adjusted_close" in frame.columns
        else pd.Series(index=frame.index, dtype=float)
    )
    close = (
        pd.to_numeric(frame["close"], errors="coerce")
        if "close" in frame.columns
        else pd.Series(index=frame.index, dtype=float)
    )
    frame["benchmark_price"] = adjusted.where(adjusted > 0, close)
    frame = frame.loc[
        frame["date"].notna()
        & frame["benchmark_price"].notna()
        & (frame["benchmark_price"] > 0)
    ].sort_values("date").drop_duplicates("date", keep="last")
    if frame.empty:
        raise EvaluationError(f"No valid {benchmark.upper()} benchmark prices")
    return frame[["date", "benchmark_price"]].reset_index(drop=True)


def _price_on(prices: pd.DataFrame, target: date, benchmark: str) -> float:
    match = prices.loc[prices["date"] == target, "benchmark_price"]
    if match.empty:
        raise EvaluationError(f"Missing exact {benchmark.upper()} benchmark price for {target}")
    return float(match.iloc[-1])


def _portfolio_value(path: Path) -> tuple[float, int]:
    if not path.exists():
        raise EvaluationError(f"Missing portfolio state: {path}")
    frame = pd.read_csv(path)
    if not {"ticker", "market_value"}.issubset(frame.columns):
        raise EvaluationError(f"Malformed portfolio state: {path}")
    values = pd.to_numeric(frame["market_value"], errors="coerce")
    if values.isna().any() or (values < 0).any():
        raise EvaluationError(f"Invalid portfolio market value in {path}")
    return float(values.sum()), int(len(frame))


def _run_cash_deployed(run_dir: Path, decision: dict) -> float:
    post_path = run_dir / "post_fill_reconciliation.json"
    post = _read_json(post_path)
    if not post.get("portfolio_state_ready"):
        raise EvaluationError(f"Post-fill portfolio not ready for {run_dir.name}")
    cash_change = post.get("cash_change")
    if cash_change in (None, ""):
        raise EvaluationError(f"Missing cash_change in {post_path}")
    deployed = -float(cash_change)
    if deployed <= 0:
        raise EvaluationError(f"Expected positive deployed capital for {run_dir.name}, got {deployed}")
    planned = float(decision.get("planned_investment") or 0.0)
    if abs(planned - deployed) > 0.02:
        raise EvaluationError(
            f"Planned/deployed mismatch for {run_dir.name}: planned={planned:.2f}, deployed={deployed:.2f}"
        )
    return deployed


def _submission_matches(run_dir: Path) -> dict[str, dict]:
    for path in (
        run_dir / "submission_reconciliation_postfill.json",
        run_dir / "submission_reconciliation.json",
    ):
        if path.exists():
            payload = _read_json(path)
            return {
                str(row.get("ticker") or "").upper(): row
                for row in (payload.get("matches") or [])
                if isinstance(row, dict) and row.get("ticker")
            }
    return {}


def _market_snapshot(run_dir: Path) -> pd.DataFrame:
    path = run_dir / "robinhood_market_snapshot_normalized.csv"
    if not path.exists():
        return pd.DataFrame(columns=["ticker", "close"])
    frame = pd.read_csv(path)
    if not {"ticker", "close"}.issubset(frame.columns):
        return pd.DataFrame(columns=["ticker", "close"])
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if "price_valid" in frame.columns:
        valid = frame["price_valid"].astype(str).str.lower().isin({"true", "1"})
        frame = frame.loc[valid]
    return frame[["ticker", "close"]].dropna()


def _selection_entry_price(
    ticker: str,
    submission: dict[str, dict],
    market: pd.DataFrame,
) -> tuple[float | None, str]:
    row = submission.get(ticker)
    if row:
        average = row.get("average_price")
        if average not in (None, "") and float(average) > 0:
            return float(average), "broker_average_fill"
        quantity = row.get("filled_quantity")
        dollars = row.get("broker_dollars") or row.get("requested_dollars")
        if quantity not in (None, "", 0, "0") and dollars not in (None, ""):
            implied = float(dollars) / float(quantity)
            if implied > 0:
                return implied, "broker_implied_fill"
    match = market.loc[market["ticker"] == ticker, "close"]
    if not match.empty and float(match.iloc[-1]) > 0:
        return float(match.iloc[-1]), "robinhood_run_snapshot"
    return None, "missing"


def _build_run_records(
    run_dirs: list[Path],
    benchmark_prices: pd.DataFrame,
    benchmark: str,
) -> tuple[list[dict], list[dict]]:
    run_records: list[dict] = []
    cohorts: list[dict] = []
    cumulative_contribution = 0.0
    benchmark_shares = 0.0

    for run_dir in run_dirs:
        run_date = date.fromisoformat(run_dir.name)
        decision = _read_json(run_dir / "shadow_decision.json")
        if str(decision.get("model_id") or "") != "long_growth_v1":
            raise EvaluationError(f"Unexpected model_id in {run_dir}: {decision.get('model_id')!r}")

        deployed = _run_cash_deployed(run_dir, decision)
        cumulative_contribution += deployed
        benchmark_entry = _price_on(benchmark_prices, run_date, benchmark)
        benchmark_shares += deployed / benchmark_entry
        benchmark_value = benchmark_shares * benchmark_entry
        portfolio_value, position_count = _portfolio_value(run_dir / "portfolio_state.csv")

        run_records.append(
            {
                "run_date": run_date,
                "decision_hash": str(decision.get("decision_hash") or ""),
                "deployed_dollars": deployed,
                "cumulative_contributed": cumulative_contribution,
                "v1_position_value": portfolio_value,
                "v1_return": portfolio_value / cumulative_contribution - 1.0,
                "benchmark": benchmark.upper(),
                "benchmark_price": benchmark_entry,
                "benchmark_shares": benchmark_shares,
                "benchmark_value": benchmark_value,
                "benchmark_return": benchmark_value / cumulative_contribution - 1.0,
                "excess_value": portfolio_value - benchmark_value,
                "excess_return": (
                    portfolio_value / cumulative_contribution
                    - benchmark_value / cumulative_contribution
                ),
                "position_count": position_count,
            }
        )

        submission = _submission_matches(run_dir)
        market = _market_snapshot(run_dir)
        for row in decision.get("decisions") or []:
            if str(row.get("status") or "") != "buy":
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            entry_price, entry_source = _selection_entry_price(ticker, submission, market)
            cohorts.append(
                {
                    "selection_date": run_date,
                    "decision_hash": str(decision.get("decision_hash") or ""),
                    "rank": int(row.get("rank")),
                    "ticker": ticker,
                    "score": float(row.get("score")),
                    "allocation_dollars": float(row.get("allocation_dollars") or 0.0),
                    "entry_price": entry_price,
                    "entry_price_source": entry_source,
                    "benchmark_entry_price": benchmark_entry,
                }
            )

    return run_records, cohorts


def _build_forward_returns(
    cohorts: pd.DataFrame,
    run_dirs: list[Path],
    benchmark_prices: pd.DataFrame,
    benchmark: str,
) -> pd.DataFrame:
    observations = {
        date.fromisoformat(run_dir.name): _market_snapshot(run_dir)
        for run_dir in run_dirs
    }
    latest_date = max(observations)
    rows: list[dict] = []

    for cohort in cohorts.to_dict("records"):
        selection_date = cohort["selection_date"]
        for horizon, days in HORIZONS.items():
            target_date = selection_date + timedelta(days=days)
            base = {
                "selection_date": selection_date,
                "decision_hash": cohort["decision_hash"],
                "rank": cohort["rank"],
                "ticker": cohort["ticker"],
                "score": cohort["score"],
                "allocation_dollars": cohort["allocation_dollars"],
                "horizon": horizon,
                "target_date": target_date,
            }
            if latest_date < target_date:
                rows.append({**base, "status": "pending", "observation_date": None,
                             "stock_return": None, "benchmark_return": None, "excess_return": None})
                continue

            eligible_dates = sorted(
                obs_date for obs_date in observations
                if target_date <= obs_date <= target_date + timedelta(days=7)
            )
            if not eligible_dates:
                rows.append({**base, "status": "missing_observation", "observation_date": None,
                             "stock_return": None, "benchmark_return": None, "excess_return": None})
                continue

            observation_date = eligible_dates[0]
            market = observations[observation_date]
            match = market.loc[market["ticker"] == cohort["ticker"], "close"]
            entry_price = cohort["entry_price"]
            if entry_price in (None, 0) or match.empty or float(match.iloc[-1]) <= 0:
                rows.append({**base, "status": "missing_price", "observation_date": observation_date,
                             "stock_return": None, "benchmark_return": None, "excess_return": None})
                continue

            benchmark_observation = _price_on(benchmark_prices, observation_date, benchmark)
            stock_return = float(match.iloc[-1]) / float(entry_price) - 1.0
            benchmark_return = benchmark_observation / float(cohort["benchmark_entry_price"]) - 1.0
            rows.append(
                {
                    **base,
                    "status": "complete",
                    "observation_date": observation_date,
                    "stock_return": stock_return,
                    "benchmark_return": benchmark_return,
                    "excess_return": stock_return - benchmark_return,
                }
            )

    return pd.DataFrame(rows)


def evaluate_v1_live_performance(
    *,
    shadow_root: str | Path,
    benchmark_prices_path: str | Path,
    benchmark: str = "SPY",
) -> EvaluationResult:
    shadow_root = Path(shadow_root)
    benchmark_prices = _load_benchmark_prices(Path(benchmark_prices_path), benchmark)
    run_dirs = _completed_run_dirs(shadow_root)
    run_records, cohort_rows = _build_run_records(run_dirs, benchmark_prices, benchmark)

    weekly = pd.DataFrame(run_records)
    cohorts = pd.DataFrame(cohort_rows)
    if cohorts.empty:
        raise EvaluationError("No live V1 buy selections found")

    latest = weekly.iloc[-1]
    summary = {
        "model_id": "long_growth_v1",
        "evaluation_only": True,
        "benchmark": benchmark.upper(),
        "benchmark_method": "matched_cash_flow",
        "benchmark_price_basis": "adjusted_close_then_close",
        "first_live_run": str(weekly.iloc[0]["run_date"]),
        "latest_live_run": str(latest["run_date"]),
        "completed_runs": int(len(weekly)),
        "cumulative_contributed": float(latest["cumulative_contributed"]),
        "v1_position_value": float(latest["v1_position_value"]),
        "v1_profit_loss": float(latest["v1_position_value"] - latest["cumulative_contributed"]),
        "v1_return": float(latest["v1_return"]),
        "benchmark_value": float(latest["benchmark_value"]),
        "benchmark_profit_loss": float(latest["benchmark_value"] - latest["cumulative_contributed"]),
        "benchmark_return": float(latest["benchmark_return"]),
        "excess_value": float(latest["excess_value"]),
        "excess_return": float(latest["excess_return"]),
        "distinct_selected_tickers": int(cohorts["ticker"].nunique()),
        "selection_events": int(len(cohorts)),
        "current_position_count": int(latest["position_count"]),
    }

    benchmark_history = weekly[
        [
            "run_date",
            "deployed_dollars",
            "cumulative_contributed",
            "benchmark",
            "benchmark_price",
            "benchmark_shares",
            "benchmark_value",
            "benchmark_return",
        ]
    ].copy()

    return EvaluationResult(
        summary=summary,
        weekly_history=weekly,
        benchmark_history=benchmark_history,
        selection_cohorts=cohorts,
        selection_forward_returns=_build_forward_returns(
            cohorts, run_dirs, benchmark_prices, benchmark
        ),
    )
