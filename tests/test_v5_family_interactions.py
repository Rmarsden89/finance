import pandas as pd

from scripts.analyze_v5_family_interactions import family_tail_summary


def test_high_quality_quartile_has_better_tail_in_fixture() -> None:
    frame = pd.DataFrame({
        'v1_rank_band':['top10']*8,
        'quality_score':[10,20,30,40,60,70,80,90],
        'fwd_13w_status':['mature']*8,
        'fwd_13w_excess_return':[-0.4,-0.3,-0.2,-0.1,0.0,0.1,0.2,0.3],
    })
    result = family_tail_summary(frame, (13,))
    row = result.loc[result['family'].eq('quality')].iloc[0]
    assert row['high_minus_low_mean_excess'] > 0
    assert row['p10_protection_high_minus_low'] > 0
