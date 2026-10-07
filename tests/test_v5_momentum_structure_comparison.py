import pandas as pd

from scripts.run_v5_momentum_structure_comparison import add_structures


def test_boundary_confirmation_swaps_rank10_q1_for_rank11_q5() -> None:
    frame = pd.DataFrame([
        {
            "decision_date": "2026-01-01",
            "ticker": f"T{i:02d}",
            "cand003_rank": i,
            "cand003_score": 101-i,
            "cand003_top_conviction_eligible": True,
            "momentum_score": 50,
            "momentum_quintile": 3,
        }
        for i in range(1, 12)
    ])
    frame.loc[frame["cand003_rank"].eq(10), "momentum_quintile"] = 1
    frame.loc[frame["cand003_rank"].eq(11), "momentum_quintile"] = 5
    result, swaps = add_structures(frame)
    assert swaps == 1
    assert not bool(result.loc[result["cand003_rank"].eq(10), "mom_conf_selected"].iloc[0])
    assert bool(result.loc[result["cand003_rank"].eq(11), "mom_conf_selected"].iloc[0])
