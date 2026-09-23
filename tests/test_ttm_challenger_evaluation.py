from __future__ import annotations

import pandas as pd

from scripts.evaluate_v2_ttm_challenger import (
    _concentration_summary,
    _turnover_summary,
    _validate_benchmark_result,
)
from finance.research.ttm_promotion_criteria import TTM_CHALLENGER


def _signal_frame(*, v2: bool) -> pd.DataFrame:
    rows = []
    for decision_date, tickers in (
        ("2020-01-03", [f"T{i:02d}" for i in range(1, 12)]),
        ("2020-01-10", [f"T{i:02d}" for i in range(2, 13)]),
    ):
        for rank, ticker in enumerate(tickers, start=1):
            row = {
                "decision_date": decision_date,
                "ticker": ticker,
                "market_cap": float(1000 - rank * 10),
            }
            if v2:
                row[f"{TTM_CHALLENGER.model_id}_score"] = float(100 - rank)
                row["v2_top_conviction_eligible"] = True
            else:
                row["long_growth_v1_score"] = float(100 - rank)
                row["top_conviction_eligible"] = True
            rows.append(row)
    return pd.DataFrame(rows)


def test_turnover_summary_uses_weekly_top10_replacements() -> None:
    v1 = _signal_frame(v2=False)
    v2 = _signal_frame(v2=True)

    result = _turnover_summary(v1, v2).set_index("variant")

    assert result.loc["v1", "valid_transitions"] == 1
    assert result.loc["v2", "valid_transitions"] == 1
    assert result.loc["v1", "mean_replacements"] == 1.0
    assert result.loc["v2", "mean_replacements"] == 1.0
    assert result.loc["v1", "mean_replacement_rate"] == 0.1
    assert result.loc["v2", "mean_replacement_rate"] == 0.1


def test_concentration_summary_uses_complete_top10_market_caps() -> None:
    v1 = _signal_frame(v2=False)
    v2 = _signal_frame(v2=True)

    result = _concentration_summary(v1, v2).set_index("variant")

    assert result.loc["v1", "valid_weeks"] == 2
    assert result.loc["v2", "valid_weeks"] == 2
    assert result.loc["v1", "mean_top10_market_cap_hhi"] > 0
    assert result.loc["v2", "mean_largest_market_cap_share"] > 0


class _Result:
    def __init__(self, summary):
        self.summary = summary


def test_benchmark_validation_rejects_zero_buy_cash_only_run() -> None:
    result = _Result({
        "buy_count": 0,
        "decision_weeks": 10,
        "unfilled_order_count": 10,
    })

    try:
        _validate_benchmark_result(
            result,
            expected_decision_weeks=10,
            benchmark_symbol="SPY",
            scope="full-period",
        )
    except SystemExit as exc:
        assert "executed zero buys" in str(exc)
    else:
        raise AssertionError("zero-buy benchmark run should fail closed")


def test_benchmark_validation_accepts_accounted_decision_weeks() -> None:
    result = _Result({
        "buy_count": 9,
        "decision_weeks": 10,
        "unfilled_order_count": 1,
    })

    _validate_benchmark_result(
        result,
        expected_decision_weeks=10,
        benchmark_symbol="SPY",
        scope="full-period",
    )


def test_historical_evaluator_defaults_to_voo_benchmark() -> None:
    source = (
        __import__("pathlib").Path(__file__).parents[1]
        / "scripts"
        / "evaluate_v2_ttm_challenger.py"
    ).read_text(encoding="utf-8")

    assert 'default=Path("data/market/benchmark_voo.csv")' in source
    assert 'parser.add_argument("--benchmark-symbol", default="VOO")' in source
