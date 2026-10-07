import pandas as pd
from scripts.analyze_v5_time_robustness import time_robustness

def test_growth_valuation_period_spread() -> None:
    rows=[]
    for i in range(40):
        rows.append({
            'v1_rank_band':'top10','decision_date':'2018-01-05',
            'growth_score':80 if i<20 else 20,
            'valuation_score':80 if i%2==0 else 20,
            'fwd_13w_status':'mature',
            'fwd_13w_excess_return':0.01 if i%2==0 else 0.05,
        })
    frame=pd.DataFrame(rows)
    result=time_robustness(frame,(13,))
    row=result.loc[result['interaction'].eq('growth_x_valuation')].iloc[0]
    assert row['low_right_minus_high_right_with_high_left'] > 0
