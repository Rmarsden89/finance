import pytest
import pandas as pd

from scripts.analyze_v5_v1_diagnostic import historical_rank_band_deltas


def test_rank_band_delta_is_nearmiss_minus_top10() -> None:
    frame = pd.DataFrame([
        {'rank_band':'top10','fwd_1w_mean_excess':0.02,'fwd_1w_median_excess':0.01,'fwd_1w_mature_rows':100},
        {'rank_band':'rank11_25','fwd_1w_mean_excess':0.03,'fwd_1w_median_excess':0.02,'fwd_1w_mature_rows':150},
    ])
    result = historical_rank_band_deltas(frame)
    assert result.iloc[0]['near_minus_top10_mean_excess'] == pytest.approx(0.01)
    assert result.iloc[0]['near_minus_top10_median_excess'] == pytest.approx(0.01)


from scripts.analyze_v5_v1_diagnostic import hypothesis_rows


def test_v3_event_count_uses_only_v2_to_v3_transition() -> None:
    attr = pd.DataFrame([
        {'transition':'v1_to_v2','direction':'exited','reason':'ttm_score_rank_displacement','events':4},
        {'transition':'v2_to_v3','direction':'entered','reason':'indirect_v3_rerank','events':1},
        {'transition':'v2_to_v3','direction':'exited','reason':'indirect_v3_rerank','events':1},
    ])
    hypotheses = hypothesis_rows(
        pd.DataFrame(),
        attr,
        {'persistent_names': [], 'one_week_names': [], 'max_distinct_weeks': 0},
        pd.DataFrame(),
    )
    row = hypotheses.loc[hypotheses['hypothesis_id'].eq('H44-03')].iloc[0]
    assert '(2 events)' in row['observation']
