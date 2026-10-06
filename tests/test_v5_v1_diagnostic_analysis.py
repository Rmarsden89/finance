import pandas as pd

from scripts.analyze_v5_v1_diagnostic import historical_rank_band_deltas


def test_rank_band_delta_is_nearmiss_minus_top10() -> None:
    frame = pd.DataFrame([
        {'rank_band':'top10','fwd_1w_mean_excess':0.02,'fwd_1w_median_excess':0.01,'fwd_1w_mature_rows':100},
        {'rank_band':'rank11_25','fwd_1w_mean_excess':0.03,'fwd_1w_median_excess':0.02,'fwd_1w_mature_rows':150},
    ])
    result = historical_rank_band_deltas(frame)
    assert result.iloc[0]['near_minus_top10_mean_excess'] == 0.01
    assert result.iloc[0]['near_minus_top10_median_excess'] == 0.01
