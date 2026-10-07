import pandas as pd

from scripts.analyze_v5_family_patterns import summarize_family_patterns


def test_family_summary_uses_top10_mature_rows_only() -> None:
    rows = []
    for idx, excess in enumerate([0.10, 0.05, -0.02, -0.10]):
        rows.append({
            'v1_rank_band':'top10',
            'quality_score':[90,80,30,20][idx],
            'fwd_4w_status':'mature',
            'fwd_4w_excess_return':excess,
        })
    rows.append({
        'v1_rank_band':'rank11_25',
        'quality_score':100,
        'fwd_4w_status':'mature',
        'fwd_4w_excess_return':1.0,
    })
    frame = pd.DataFrame(rows)
    summary, _ = summarize_family_patterns(frame, (4,))
    quality = summary.loc[summary['family'].eq('quality')].iloc[0]
    assert quality['mature_rows'] == 4
    assert quality['family_rows'] == 4
    assert quality['upper_minus_lower_mean_score'] > 0
    assert quality['spearman_score_vs_excess_return'] > 0
