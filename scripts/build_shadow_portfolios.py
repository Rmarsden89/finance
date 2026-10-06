from __future__ import annotations

"""Build matched-cash-flow, no-sell shadow selection portfolios.

Each registered shadow model contributes the same dollars actually deployed by
V1 on that completed run date. Shadow buys use the model's own immutable
post-computation execution-price capture. Existing shadow holdings are marked
at that same capture timestamp. No broker/order action is possible here.
"""

import argparse
import csv
from datetime import date
import json
import math
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def render(root: Path, template: str, as_of: str) -> Path:
    path = Path(template.format(as_of=as_of))
    return path if path.is_absolute() else root / path


def load_registry(root: Path, path: Path) -> list[dict[str, Any]]:
    registry_path = path if path.is_absolute() else root / path
    payload = read_json(registry_path)
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported shadow registry schema")
    modes = payload.get("modes")
    if not isinstance(modes, list):
        raise ValueError("Shadow registry modes must be a list")
    output = []
    for mode in modes:
        if not isinstance(mode, dict):
            raise ValueError("Malformed shadow mode")
        required = ("id", "decision", "summary", "expected_status", "execution_capture")
        missing = [name for name in required if not str(mode.get(name) or "").strip()]
        if missing:
            # A research mode without an execution-capture contract simply does
            # not participate in virtual-portfolio performance.
            continue
        output.append(dict(mode))
    return output


def decision_top10(decision: dict[str, Any]) -> list[str]:
    rows = decision.get("top10")
    if rows is None:
        rows = decision.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("Decision is missing ranked rows")
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed decision row")
        rank = int(row["rank"])
        if rank <= 10:
            ranked.append((rank, str(row["ticker"]).strip().upper()))
    ranked.sort()
    if [rank for rank, _ in ranked] != list(range(1, 11)):
        raise ValueError("Expected Top-10 ranks 1..10")
    tickers = [ticker for _, ticker in ranked]
    if len(set(tickers)) != 10 or any(not ticker for ticker in tickers):
        raise ValueError("Expected 10 unique Top-10 tickers")
    return tickers


def quote_map(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = capture.get("quotes")
    if not isinstance(rows, list):
        raise ValueError("Execution capture is missing quotes")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed execution quote")
        ticker = str(row.get("ticker") or "").strip().upper()
        price = float(row.get("price") or 0.0)
        if not ticker or not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid execution quote")
        if ticker in result:
            raise ValueError(f"Duplicate execution quote for {ticker}")
        result[ticker] = row
    return result


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def historical_signal_quotes(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ticker", "close", "price_valid", "price_timestamp", "price_source"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Historical signal snapshot missing columns {sorted(missing)}: {path}"
            )
        for row in reader:
            if not _truthy(row.get("price_valid")):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            try:
                price = float(row.get("close") or 0.0)
            except ValueError:
                continue
            timestamp = str(row.get("price_timestamp") or "").strip()
            if not ticker or not timestamp or not math.isfinite(price) or price <= 0:
                continue
            if ticker in result:
                raise ValueError(
                    f"Duplicate historical signal quote for {ticker}: {path}"
                )
            result[ticker] = {
                "ticker": ticker,
                "price": price,
                "price_field": "close",
                "quote_timestamp": timestamp,
                "price_source": str(row.get("price_source") or ""),
            }
    return result


def historical_spy_quote(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = read_json(path)
    try:
        price = float(payload.get("price") or 0.0)
    except (TypeError, ValueError):
        return None
    timestamp = str(payload.get("quote_timestamp") or "").strip()
    if not timestamp or not math.isfinite(price) or price <= 0:
        return None
    return {
        "ticker": "SPY",
        "price": price,
        "price_field": str(payload.get("price_field") or ""),
        "quote_timestamp": timestamp,
        "price_source": str(payload.get("source") or "robinhood"),
    }


def _latest_timestamp(quotes: dict[str, dict[str, Any]], tickers: set[str]) -> str:
    values = sorted(
        str(quotes[ticker].get("quote_timestamp") or "")
        for ticker in tickers
        if ticker in quotes and quotes[ticker].get("quote_timestamp")
    )
    return values[-1] if values else ""


def pricing_for_observation(
    root: Path,
    mode: dict[str, Any],
    *,
    as_of: str,
    selected: list[str],
    held: set[str],
) -> dict[str, Any] | None:
    capture_path = render(root, str(mode["execution_capture"]), as_of)
    if capture_path.exists():
        capture = read_json(capture_path)
        return {
            "quotes": quote_map(capture),
            "capture_timestamp": str(capture.get("captured_at") or ""),
            "price_policy": "post_computation_execution_capture",
            "benchmark_policy": "same_model_post_computation_spy_capture",
            "capture": capture,
        }

    start_text = str(mode.get("execution_capture_start_date") or "").strip()
    if start_text and date.fromisoformat(as_of) >= date.fromisoformat(start_text):
        return None

    historical_template = str(mode.get("historical_signal_snapshot") or "").strip()
    benchmark_template = str(mode.get("historical_benchmark_capture") or "").strip()
    if not historical_template or not benchmark_template:
        return None

    quotes = historical_signal_quotes(render(root, historical_template, as_of))
    spy = historical_spy_quote(render(root, benchmark_template, as_of))
    if not quotes or spy is None:
        return None
    quotes["SPY"] = spy
    stock_tickers = set(selected) | set(held)
    return {
        "quotes": quotes,
        "capture_timestamp": _latest_timestamp(quotes, stock_tickers),
        "price_policy": "historical_signal_snapshot",
        "benchmark_policy": "historical_v1_benchmark_capture",
        "capture": None,
    }


def completed_v1_runs(root: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    run_root = root / "reports" / "shadow"
    if not run_root.exists():
        return output
    for child in sorted(run_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            as_of = date.fromisoformat(child.name).isoformat()
        except ValueError:
            continue
        state_path = child / "workflow_state.json"
        package_path = child / "evaluation_package.json"
        if not state_path.exists() or not package_path.exists():
            continue
        if read_json(state_path).get("status") != "COMPLETE":
            continue
        package = read_json(package_path)
        deployed = float(package.get("deployed_contribution") or 0.0)
        if deployed <= 0 or deployed > 10.0 + 1e-9:
            raise ValueError(f"Invalid V1 deployed contribution for {as_of}: {deployed}")
        output[as_of] = package
    return output


def build_model_portfolio(
    root: Path,
    mode: dict[str, Any],
    v1_runs: dict[str, dict[str, Any]],
) -> tuple[list[dict], list[dict], list[dict], dict]:
    model_id = str(mode["id"])
    holdings: dict[str, float] = {}
    spy_shares = 0.0
    cumulative = 0.0
    history: list[dict] = []
    transactions: list[dict] = []
    excluded: list[dict] = []
    last_quotes: dict[str, dict[str, Any]] = {}

    for as_of in sorted(v1_runs):
        decision_path = render(root, str(mode["decision"]), as_of)
        summary_path = render(root, str(mode["summary"]), as_of)
        if not decision_path.exists() or not summary_path.exists():
            continue

        decision = read_json(decision_path)
        summary = read_json(summary_path)
        v1_hash = str(v1_runs[as_of].get("decision_hash") or "")
        decision_hash = str(decision.get("decision_hash") or "")

        if summary.get("status") != mode["expected_status"]:
            raise ValueError(f"{model_id} invalid completion status for {as_of}")
        if summary.get("v1_decision_hash") != v1_hash:
            raise ValueError(f"{model_id} V1 hash mismatch for {as_of}")
        if str(decision.get("v1_decision_hash") or "") != v1_hash:
            raise ValueError(f"{model_id} decision V1 hash mismatch for {as_of}")

        selected = decision_top10(decision)
        pricing = pricing_for_observation(
            root,
            mode,
            as_of=as_of,
            selected=selected,
            held=set(holdings),
        )
        if pricing is None:
            start_text = str(mode.get("execution_capture_start_date") or "").strip()
            reason = (
                "missing_prospective_execution_capture"
                if start_text and date.fromisoformat(as_of) >= date.fromisoformat(start_text)
                else "missing_historical_signal_or_benchmark_price"
            )
            excluded.append({"as_of": as_of, "reason": reason})
            continue

        capture = pricing["capture"]
        if capture is not None:
            if capture.get("status") != "SHADOW_EXECUTION_PRICE_CAPTURE_COMPLETE":
                raise ValueError(f"{model_id} incomplete execution capture for {as_of}")
            if capture.get("model_id") != model_id:
                raise ValueError(f"{model_id} execution capture model mismatch for {as_of}")
            if capture.get("decision_hash") != decision_hash:
                raise ValueError(f"{model_id} execution capture decision mismatch for {as_of}")
            if capture.get("broker_order_capability") is not False:
                raise ValueError(f"{model_id} capture unexpectedly enables order capability")

        quotes = pricing["quotes"]
        missing_held = sorted(ticker for ticker in holdings if ticker not in quotes)
        if missing_held:
            raise ValueError(
                f"{model_id} capture cannot mark existing holdings for {as_of}: "
                + ", ".join(missing_held)
            )
        missing_selected = sorted(ticker for ticker in selected if ticker not in quotes)
        if missing_selected:
            raise ValueError(
                f"{model_id} capture missing selected quotes for {as_of}: "
                + ", ".join(missing_selected)
            )
        if "SPY" not in quotes:
            raise ValueError(f"{model_id} capture missing SPY for {as_of}")

        pre_value = sum(
            quantity * float(quotes[ticker]["price"])
            for ticker, quantity in holdings.items()
        )
        spy_pre_value = spy_shares * float(quotes["SPY"]["price"])
        contribution = float(v1_runs[as_of]["deployed_contribution"])
        allocation = contribution / len(selected)

        for rank, ticker in enumerate(selected, start=1):
            price = float(quotes[ticker]["price"])
            shares_added = allocation / price
            holdings[ticker] = holdings.get(ticker, 0.0) + shares_added
            transactions.append({
                "as_of": as_of,
                "model_id": model_id,
                "decision_hash": decision_hash,
                "capture_timestamp": pricing["capture_timestamp"],
                "price_policy": pricing["price_policy"],
                "benchmark_policy": pricing["benchmark_policy"],
                "rank": rank,
                "ticker": ticker,
                "allocation_dollars": allocation,
                "execution_price": price,
                "shares_added": shares_added,
                "price_field": quotes[ticker].get("price_field"),
                "quote_timestamp": quotes[ticker].get("quote_timestamp"),
            })

        spy_price = float(quotes["SPY"]["price"])
        spy_shares += contribution / spy_price
        cumulative += contribution
        post_value = pre_value + contribution
        spy_post_value = spy_pre_value + contribution
        profit_loss = post_value - cumulative
        spy_profit_loss = spy_post_value - cumulative
        portfolio_return = profit_loss / cumulative
        spy_return = spy_profit_loss / cumulative

        history.append({
            "as_of": as_of,
            "model_id": model_id,
            "decision_hash": decision_hash,
            "capture_timestamp": pricing["capture_timestamp"],
            "price_policy": pricing["price_policy"],
            "benchmark_policy": pricing["benchmark_policy"],
            "deployed_contribution": contribution,
            "cumulative_contributed": cumulative,
            "pre_contribution_value": pre_value,
            "post_contribution_value": post_value,
            "profit_loss": profit_loss,
            "deployed_capital_return": portfolio_return,
            "spy_price": spy_price,
            "spy_quote_timestamp": quotes["SPY"].get("quote_timestamp"),
            "spy_post_contribution_value": spy_post_value,
            "spy_profit_loss": spy_profit_loss,
            "spy_deployed_capital_return": spy_return,
            "excess_value_vs_spy": post_value - spy_post_value,
            "excess_return_vs_spy": portfolio_return - spy_return,
            "position_count": len(holdings),
        })
        last_quotes = quotes

    positions: list[dict] = []
    if history:
        for ticker, quantity in sorted(holdings.items()):
            price = float(last_quotes[ticker]["price"])
            positions.append({
                "model_id": model_id,
                "ticker": ticker,
                "quantity": quantity,
                "latest_price": price,
                "latest_market_value": quantity * price,
                "latest_capture_timestamp": history[-1]["capture_timestamp"],
            })

    if history:
        latest = history[-1]
        summary = {
            "model_id": model_id,
            "portfolio_status": "active",
            "first_portfolio_date": history[0]["as_of"],
            "latest_portfolio_date": latest["as_of"],
            "contribution_events": len(history),
            "cumulative_contributed": latest["cumulative_contributed"],
            "portfolio_value": latest["post_contribution_value"],
            "profit_loss": latest["profit_loss"],
            "deployed_capital_return": latest["deployed_capital_return"],
            "spy_value": latest["spy_post_contribution_value"],
            "spy_deployed_capital_return": latest["spy_deployed_capital_return"],
            "excess_value_vs_spy": latest["excess_value_vs_spy"],
            "excess_return_vs_spy": latest["excess_return_vs_spy"],
            "position_count": latest["position_count"],
            "historical_observations_excluded_for_missing_capture": len(excluded),
            "historical_signal_price_events": sum(
                row.get("price_policy") == "historical_signal_snapshot"
                for row in history
            ),
            "prospective_execution_capture_events": sum(
                row.get("price_policy") == "post_computation_execution_capture"
                for row in history
            ),
            "execution_policy": (
                "equal_split_of_actual_v1_deployed_contribution_across_saved_top10"
            ),
            "timing_policy": (
                "historical_signal_snapshot_before_capture_start; "
                "own_post_computation_capture_on_or_after_capture_start"
            ),
            "sell_policy": "no_discretionary_selling",
            "actual_broker_performance": False,
        }
    else:
        summary = {
            "model_id": model_id,
            "portfolio_status": "awaiting_first_execution_capture",
            "contribution_events": 0,
            "historical_observations_excluded_for_missing_capture": len(excluded),
            "historical_signal_price_events": 0,
            "prospective_execution_capture_events": 0,
            "actual_broker_performance": False,
        }

    return history, transactions, positions, {"summary": summary, "excluded": excluded}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only matched-cash-flow shadow portfolio evaluator"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--registry", type=Path, default=Path("config/live_shadow_modes.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports/model_comparison/portfolios")
    )
    args = parser.parse_args()
    root = args.repo_root.resolve()
    modes = load_registry(root, args.registry)
    runs = completed_v1_runs(root)
    out_root = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    combined = {
        "schema_version": 1,
        "evaluation_only": True,
        "broker_order_capability": False,
        "models": {},
    }

    for mode in modes:
        history, transactions, positions, result = build_model_portfolio(
            root, mode, runs
        )
        model_dir = out_root / str(mode["id"])
        model_dir.mkdir(parents=True, exist_ok=True)
        write_csv(model_dir / "portfolio_history.csv", history, [
            "as_of", "model_id", "decision_hash", "capture_timestamp",
            "price_policy", "benchmark_policy",
            "deployed_contribution", "cumulative_contributed",
            "pre_contribution_value", "post_contribution_value", "profit_loss",
            "deployed_capital_return", "spy_price", "spy_quote_timestamp",
            "spy_post_contribution_value", "spy_profit_loss",
            "spy_deployed_capital_return", "excess_value_vs_spy",
            "excess_return_vs_spy", "position_count",
        ])
        write_csv(model_dir / "transactions.csv", transactions, [
            "as_of", "model_id", "decision_hash", "capture_timestamp",
            "price_policy", "benchmark_policy",
            "rank", "ticker", "allocation_dollars", "execution_price",
            "shares_added", "price_field", "quote_timestamp",
        ])
        write_csv(model_dir / "positions.csv", positions, [
            "model_id", "ticker", "quantity", "latest_price",
            "latest_market_value", "latest_capture_timestamp",
        ])
        (model_dir / "summary.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        combined["models"][str(mode["id"])] = result["summary"]

    (out_root / "summary.json").write_text(
        json.dumps(combined, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("MATCHED-CASH-FLOW SHADOW PORTFOLIOS")
    print("Evaluation only:          YES")
    print("Broker/order capability:  NONE")
    for model_id, summary in combined["models"].items():
        if summary["portfolio_status"] == "active":
            print(
                f"{model_id}: {summary['contribution_events']} event(s), "
                f"value=${summary['portfolio_value']:.2f}, "
                f"return={summary['deployed_capital_return']:+.2%}, "
                f"excess vs SPY={summary['excess_return_vs_spy']:+.2%}"
            )
        else:
            print(
                f"{model_id}: awaiting usable shadow pricing evidence; "
                f"observations excluded="
                f"{summary['historical_observations_excluded_for_missing_capture']}"
            )
    print(f"Output:                   {out_root}")


if __name__ == "__main__":
    main()
