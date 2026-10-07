import pandas as pd


def test_candidate_comparison_fixture() -> None:
    frame = pd.DataFrame([
        {"cand1_xirr_delta": 0.01, "cand2_xirr_delta": -0.01},
        {"cand1_xirr_delta": 0.02, "cand2_xirr_delta": 0.01},
    ])
    c1 = frame["cand1_xirr_delta"].gt(0)
    c2 = frame["cand2_xirr_delta"].gt(0)
    assert int(c1.sum()) == 2
    assert int(c2.sum()) == 1
    assert int(c1.ne(c2).sum()) == 1
