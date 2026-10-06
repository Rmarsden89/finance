import pandas as pd

from scripts.analyze_v5_fund_cand_002_windows import classify_window


def test_classify_window() -> None:
    assert classify_window(pd.Series({"xirr_delta": 0.01})) == "WIN"
    assert classify_window(pd.Series({"xirr_delta": -0.01})) == "LOSS"
    assert classify_window(pd.Series({"xirr_delta": 0.0})) == "LOSS"
