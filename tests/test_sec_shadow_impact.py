import pandas as pd

from finance.data.sec_shadow_impact import compare_sec_latest_state


def fact(*, cik=1, concept="revenue", period="2026-03-31", accepted="2026-05-01T10:00:00", value=100, adsh="A"):
    return {
        "cik": cik,
        "concept": concept,
        "ddate_date": period,
        "period_date": period,
        "filed_date": "2026-05-01",
        "accepted_at": accepted,
        "value": value,
        "source_tag": "Revenues",
        "adsh": adsh,
        "qtrs": 1,
        "uom": "USD",
        "form": "10-Q",
    }


def test_detects_fresher_period() -> None:
    historical = pd.DataFrame([fact()])
    shadow = pd.DataFrame([
        fact(),
        fact(period="2026-06-30", accepted="2026-08-01T10:00:00", value=110, adsh="B"),
    ])
    universe = pd.DataFrame([{"ticker": "AAA", "cik": 1}])

    detail, by_ticker, summary, _ = compare_sec_latest_state(
        historical,
        shadow,
        as_of=pd.Timestamp("2026-09-09T16:00:00"),
        universe=universe,
    )

    assert len(detail) == 1
    assert detail.iloc[0]["status"] == "fresher_period"
    assert summary.fresher_period_groups == 1
    assert by_ticker.iloc[0]["ticker"] == "AAA"


def test_ignores_unchanged_latest_fact() -> None:
    historical = pd.DataFrame([fact()])
    shadow = pd.DataFrame([fact()])
    universe = pd.DataFrame([{"ticker": "AAA", "cik": 1}])

    detail, _, summary, _ = compare_sec_latest_state(
        historical,
        shadow,
        as_of=pd.Timestamp("2026-09-09T16:00:00"),
        universe=universe,
    )

    assert detail.empty
    assert summary.changed_fact_groups == 0
