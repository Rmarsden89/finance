import pandas as pd

from scripts.build_v5_v1_diagnostic import build_historical_rank_band_summary


def test_historical_rank_band_summary_keeps_top10_and_nearmiss() -> None:
    frame = pd.DataFrame([
        {'v1_rank_band':'top10','quality_score':80,'fwd_1w_status':'mature','fwd_1w_excess_return':0.10,
         'fwd_4w_status':'pending','fwd_4w_excess_return':None,'fwd_13w_status':'pending','fwd_13w_excess_return':None,
         'fwd_26w_status':'pending','fwd_26w_excess_return':None,'fwd_52w_status':'pending','fwd_52w_excess_return':None},
        {'v1_rank_band':'rank11_25','quality_score':70,'fwd_1w_status':'mature','fwd_1w_excess_return':0.20,
         'fwd_4w_status':'pending','fwd_4w_excess_return':None,'fwd_13w_status':'pending','fwd_13w_excess_return':None,
         'fwd_26w_status':'pending','fwd_26w_excess_return':None,'fwd_52w_status':'pending','fwd_52w_excess_return':None},
    ])
    result = build_historical_rank_band_summary(frame)
    assert set(result['rank_band']) == {'top10','rank11_25'}
    top = result.loc[result['rank_band'].eq('top10')].iloc[0]
    near = result.loc[result['rank_band'].eq('rank11_25')].iloc[0]
    assert top['fwd_1w_mean_excess'] == 0.10
    assert near['fwd_1w_mean_excess'] == 0.20


from pathlib import Path

from scripts.build_v5_v1_diagnostic import build_live_top25


def test_build_live_top25_formats_iso_week(tmp_path: Path) -> None:
    root = tmp_path
    as_of = "2026-09-21"
    score_dir = (
        root / "reports" / "v2" / "long_growth_v2_research" / as_of
        / "ttm_shadow"
    )
    score_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "long_growth_v1_score": 90.0,
                "top_conviction_eligible": True,
                "quality_score": 80.0,
            },
            {
                "ticker": "BBB",
                "long_growth_v1_score": 80.0,
                "top_conviction_eligible": True,
                "quality_score": 70.0,
            },
        ]
    ).to_csv(score_dir / "v2_current_scores.csv", index=False)

    selections = pd.DataFrame(
        [{"as_of": as_of, "model_id": "v1", "ticker": "AAA"}]
    )
    result = build_live_top25(root, selections)

    assert set(result["iso_week"]) == {"2026-W39"}
    assert result.iloc[0]["v1_rank"] == 1
