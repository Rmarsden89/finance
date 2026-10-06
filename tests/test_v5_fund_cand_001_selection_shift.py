import pandas as pd

from scripts.analyze_v5_fund_cand_001_selection_shift import summarize_tickers


def test_summary_counts_candidate_only_selection_weeks() -> None:
    ranked = pd.DataFrame([
        {
            "ticker":"NVDA","v1_top10":True,"candidate_top10":True,
            "growth_minus_valuation":20.0,"candidate_minus_v1_score":2.0,
            "v1_rank":5,"candidate_rank":3,
        },
        {
            "ticker":"NVDA","v1_top10":False,"candidate_top10":True,
            "growth_minus_valuation":30.0,"candidate_minus_v1_score":3.0,
            "v1_rank":12,"candidate_rank":8,
        },
        {
            "ticker":"NVDA","v1_top10":False,"candidate_top10":False,
            "growth_minus_valuation":10.0,"candidate_minus_v1_score":1.0,
            "v1_rank":20,"candidate_rank":15,
        },
    ])
    result = summarize_tickers(ranked)
    row = result.iloc[0]
    assert row["v1_top10_weeks"] == 1
    assert row["candidate_top10_weeks"] == 2
    assert row["candidate_only_weeks"] == 1
    assert row["top10_week_delta"] == 1
    assert row["candidate_only_mean_v1_rank"] == 12
    assert row["candidate_only_mean_candidate_rank"] == 8
