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
    selection_diagnostics: pd.DataFrame
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


def _benchmark_price_for_run(
    run_dir: Path,
    benchmark_prices: pd.DataFrame,
    benchmark: str,
) -> tuple[float, str, str | None]:
    package_path = run_dir / "evaluation_package.json"
    capture_path = run_dir / "benchmark_spy_capture.json"

    capture = None
    if package_path.exists():
        package = _read_json(package_path)
        benchmark_payload = package.get("benchmark") or {}
        if str(benchmark_payload.get("symbol") or benchmark).upper() == benchmark.upper():
            capture = benchmark_payload.get("capture")
    if capture is None and capture_path.exists():
        capture = _read_json(capture_path)

    if isinstance(capture, dict):
        price = capture.get("price")
        symbol = str(capture.get("symbol") or benchmark).upper()
        if symbol == benchmark.upper() and price not in (None, "") and float(price) > 0:
            return (
                float(price),
                "intraday_run_capture",
                capture.get("quote_timestamp") or capture.get("captured_at"),
            )

    run_date = date.fromisoformat(run_dir.name)
    return (
        _price_on(benchmark_prices, run_date, benchmark),
        "daily_adjusted_close_fallback",
        None,
    )


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
    previous_post_cash: float | None = None
    cash_accounting_complete = True

    for run_dir in run_dirs:
        run_date = date.fromisoformat(run_dir.name)
        package_path = run_dir / "evaluation_package.json"
        package = _read_json(package_path) if package_path.exists() else None

        if package is not None:
            if str(package.get("model_id") or "") != "long_growth_v1":
                raise EvaluationError(
                    f"Unexpected model_id in {package_path}: {package.get('model_id')!r}"
                )
            if package.get("evaluation_only") is not True:
                raise EvaluationError(
                    f"Evaluation package is missing evaluation_only=true: {package_path}"
                )
            decision_hash = str(package.get("decision_hash") or "")
            deployed = float(package.get("deployed_contribution") or 0.0)
            portfolio_value = float(package.get("postfill_position_value") or 0.0)
            position_count = int(package.get("postfill_position_count") or 0)
            package_selections = package.get("selections") or []
            raw_pre_cash = package.get("account_cash_presubmit")
            raw_post_cash = package.get("account_cash_postfill")
            if raw_pre_cash in (None, "") or raw_post_cash in (None, ""):
                pre_cash = None
                post_cash = None
                cash_accounting_complete = False
            else:
                pre_cash = float(raw_pre_cash)
                post_cash = float(raw_post_cash)
            decision = {
                "model_id": "long_growth_v1",
                "decision_hash": decision_hash,
                "planned_investment": deployed,
                "decisions": [
                    {
                        "ticker": row.get("ticker"),
                        "rank": row.get("rank"),
                        "score": row.get("score"),
                        "status": "buy",
                        "allocation_dollars": row.get("allocation_dollars"),
                        "average_price": row.get("average_price"),
                    }
                    for row in package_selections
                ],
            }
            if deployed <= 0 or portfolio_value < 0 or position_count < 0:
                raise EvaluationError(f"Invalid evaluation package values: {package_path}")
        else:
            decision = _read_json(run_dir / "shadow_decision.json")
            if str(decision.get("model_id") or "") != "long_growth_v1":
                raise EvaluationError(
                    f"Unexpected model_id in {run_dir}: {decision.get('model_id')!r}"
                )
            decision_hash = str(decision.get("decision_hash") or "")
            deployed = _run_cash_deployed(run_dir, decision)
            portfolio_value, position_count = _portfolio_value(
                run_dir / "portfolio_state.csv"
            )
            pre_cash = None
            post_cash = None
            cash_accounting_complete = False

        inter_run_cash_drift = None
        cash_accounting_status = "not_available"
        if pre_cash is not None and post_cash is not None:
            if previous_post_cash is None:
                cash_accounting_status = "baseline"
                inter_run_cash_drift = 0.0
            else:
                inter_run_cash_drift = pre_cash - previous_post_cash
                if abs(inter_run_cash_drift) > 0.02:
                    raise EvaluationError(
                        "Unclassified inter-run account cash drift detected before "
                        f"{run_date}: previous_post={previous_post_cash:.2f}, "
                        f"current_pre={pre_cash:.2f}, drift={inter_run_cash_drift:+.2f}. "
                        "Evaluation fails closed until the cash event is classified."
                    )
                cash_accounting_status = "verified_no_unclassified_cash_drift"
            previous_post_cash = post_cash

        cumulative_contribution += deployed
        benchmark_entry, benchmark_price_source, benchmark_quote_timestamp = _benchmark_price_for_run(
            run_dir, benchmark_prices, benchmark
        )
        benchmark_shares += deployed / benchmark_entry
        benchmark_value = benchmark_shares * benchmark_entry
        strategy_cash_value = 0.0
        sleeve_value = portfolio_value + strategy_cash_value
        v1_deployed_capital_return = (
            sleeve_value / cumulative_contribution - 1.0
        )
        benchmark_deployed_capital_return = (
            benchmark_value / cumulative_contribution - 1.0
        )
        excess_deployed_capital_return = (
            v1_deployed_capital_return - benchmark_deployed_capital_return
        )

        run_records.append(
            {
                "run_date": run_date,
                "decision_hash": decision_hash,
                "deployed_dollars": deployed,
                "cumulative_contributed": cumulative_contribution,
                "v1_position_value": portfolio_value,
                "strategy_cash_value": strategy_cash_value,
                "v1_sleeve_value": sleeve_value,
                "v1_deployed_capital_return": v1_deployed_capital_return,
                "v1_return": v1_deployed_capital_return,
                "account_cash_presubmit": pre_cash,
                "account_cash_postfill": post_cash,
                "inter_run_cash_drift": inter_run_cash_drift,
                "cash_accounting_status": cash_accounting_status,
                "benchmark": benchmark.upper(),
                "benchmark_price": benchmark_entry,
                "benchmark_price_source": benchmark_price_source,
                "benchmark_quote_timestamp": benchmark_quote_timestamp,
                "benchmark_shares": benchmark_shares,
                "benchmark_value": benchmark_value,
                "benchmark_deployed_capital_return": benchmark_deployed_capital_return,
                "benchmark_return": benchmark_deployed_capital_return,
                "excess_value": sleeve_value - benchmark_value,
                "excess_deployed_capital_return": excess_deployed_capital_return,
                "excess_return": excess_deployed_capital_return,
                "position_count": position_count,
            }
        )

        submission = _submission_matches(run_dir)
        market = _market_snapshot(run_dir)
        for row in decision.get("decisions") or []:
            if str(row.get("status") or "") != "buy":
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            package_average = row.get("average_price")
            if package_average not in (None, "") and float(package_average) > 0:
                entry_price = float(package_average)
                entry_source = "evaluation_package_average_fill"
            else:
                entry_price, entry_source = _selection_entry_price(
                    ticker, submission, market
                )
            cohorts.append(
                {
                    "selection_date": run_date,
                    "decision_hash": decision_hash,
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


def _build_selection_diagnostics(cohorts: pd.DataFrame) -> pd.DataFrame:
    run_dates = sorted(cohorts["selection_date"].dropna().unique())
    rows: list[dict[str, Any]] = []
    previous_date = None
    previous: set[str] | None = None

    for run_date in run_dates:
        current = set(
            cohorts.loc[cohorts["selection_date"] == run_date, "ticker"]
            .astype(str)
            .str.upper()
        )
        if previous is None:
            retained: set[str] = set()
            new_entries = current
            dropped: set[str] = set()
            retention_rate = None
            new_entry_rate = 1.0 if current else None
        else:
            retained = previous & current
            new_entries = current - previous
            dropped = previous - current
            retention_rate = len(retained) / len(previous) if previous else None
            new_entry_rate = len(new_entries) / len(current) if current else None

        rows.append(
            {
                "run_date": run_date,
                "previous_run_date": previous_date,
                "selected_count": len(current),
                "retained_count": len(retained),
                "new_entry_count": len(new_entries),
                "dropped_count": len(dropped),
                "selection_retention_rate": retention_rate,
                "new_entry_rate": new_entry_rate,
                "retained_tickers": ",".join(sorted(retained)),
                "new_entry_tickers": ",".join(sorted(new_entries)),
                "dropped_tickers": ",".join(sorted(dropped)),
            }
        )
        previous_date = run_date
        previous = current

    return pd.DataFrame(rows)


def _build_forward_returns(
    cohorts: pd.DataFrame,
    run_dirs: list[Path],
    benchmark_prices: pd.DataFrame,
    benchmark: str,
) -> pd.DataFrame:
    run_by_date = {date.fromisoformat(run_dir.name): run_dir for run_dir in run_dirs}
    observations = {
        run_date: _market_snapshot(run_dir)
        for run_date, run_dir in run_by_date.items()
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

            benchmark_observation, _, _ = _benchmark_price_for_run(
                run_by_date[observation_date], benchmark_prices, benchmark
            )
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

    diagnostics = _build_selection_diagnostics(cohorts)
    selection_counts = cohorts["ticker"].value_counts()
    repeated_tickers = sorted(selection_counts[selection_counts > 1].index.tolist())
    one_off_tickers = sorted(selection_counts[selection_counts == 1].index.tolist())
    latest_diag = diagnostics.iloc[-1] if not diagnostics.empty else None
    latest_new_entries = (
        []
        if latest_diag is None or not latest_diag["new_entry_tickers"]
        else str(latest_diag["new_entry_tickers"]).split(",")
    )
    latest_dropped = (
        []
        if latest_diag is None or not latest_diag["dropped_tickers"]
        else str(latest_diag["dropped_tickers"]).split(",")
    )

    latest = weekly.iloc[-1]
    summary = {
        "model_id": "long_growth_v1",
        "evaluation_only": True,
        "benchmark": benchmark.upper(),
        "benchmark_method": "matched_cash_flow",
        "benchmark_price_basis": "intraday_run_capture_preferred_daily_adjusted_close_fallback",
        "first_live_run": str(weekly.iloc[0]["run_date"]),
        "latest_live_run": str(latest["run_date"]),
        "completed_runs": int(len(weekly)),
        "cumulative_contributed": float(latest["cumulative_contributed"]),
        "v1_position_value": float(latest["v1_position_value"]),
        "strategy_cash_value": float(latest["strategy_cash_value"]),
        "v1_sleeve_value": float(latest["v1_sleeve_value"]),
        "v1_profit_loss": float(latest["v1_sleeve_value"] - latest["cumulative_contributed"]),
        "v1_deployed_capital_return": float(latest["v1_deployed_capital_return"]),
        "v1_return": float(latest["v1_deployed_capital_return"]),
        "return_method": "gain_divided_by_cumulative_deployed_not_irr",
        "cash_accounting_complete": bool(cash_accounting_complete),
        "cash_accounting_status": (
            "verified_no_unclassified_cash_drift"
            if cash_accounting_complete
            else "legacy_or_missing_cash_provenance"
        ),
        "benchmark_value": float(latest["benchmark_value"]),
        "benchmark_profit_loss": float(latest["benchmark_value"] - latest["cumulative_contributed"]),
        "benchmark_deployed_capital_return": float(
            latest["benchmark_deployed_capital_return"]
        ),
        "benchmark_return": float(latest["benchmark_deployed_capital_return"]),
        "excess_value": float(latest["excess_value"]),
        "excess_deployed_capital_return": float(
            latest["excess_deployed_capital_return"]
        ),
        "excess_return": float(latest["excess_deployed_capital_return"]),
        "distinct_selected_tickers": int(cohorts["ticker"].nunique()),
        "selection_events": int(len(cohorts)),
        "repeated_selection_tickers": repeated_tickers,
        "one_off_selection_tickers": one_off_tickers,
        "latest_new_entry_tickers": latest_new_entries,
        "latest_dropped_tickers": latest_dropped,
        "latest_selection_retention_rate": (
            None
            if latest_diag is None
            or pd.isna(latest_diag["selection_retention_rate"])
            else float(latest_diag["selection_retention_rate"])
        ),
        "current_position_count": int(latest["position_count"]),
    }

    benchmark_history = weekly[
        [
            "run_date",
            "deployed_dollars",
            "cumulative_contributed",
            "benchmark",
            "benchmark_price",
            "benchmark_price_source",
            "benchmark_quote_timestamp",
            "benchmark_shares",
            "benchmark_value",
            "benchmark_deployed_capital_return",
            "benchmark_return",
        ]
    ].copy()

    return EvaluationResult(
        summary=summary,
        weekly_history=weekly,
        benchmark_history=benchmark_history,
        selection_cohorts=cohorts,
        selection_diagnostics=diagnostics,
        selection_forward_returns=_build_forward_returns(
            cohorts, run_dirs, benchmark_prices, benchmark
        ),
    )
