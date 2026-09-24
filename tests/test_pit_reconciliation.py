import pandas as pd

from finance.research.pit_reconciliation import (
    audit_panel_availability, eastern_timestamp, reconcile_panel,
)


def _fact(accepted, filed, value, accession, period="2017-09-30", qtrs=1):
    return dict(cik=1564708, concept="revenue", value=value, accepted_at=accepted,
                filed_date=filed, ddate_date=period, period_date=period,
                adsh=accession, source_tag="Revenues", form="10-Q", qtrs=qtrs)


def _row(date, fact, ticker="NWS", annual=False):
    base = "annual_revenue" if annual else "revenue"
    result = dict(decision_date=date, as_of=date + " 16:00:00", ticker=ticker,
                  cik=1564708, close=10.0, return_price=10.0)
    result.update({base: fact["value"], base + "_accepted_at": fact["accepted_at"],
                   base + "_period_date": fact["ddate_date"]})
    if not annual:
        result.update({base + "_filed_date": fact["filed_date"], base + "_adsh": fact["adsh"]})
    return result


def test_delayed_news_filing_reselects_previous_fact_for_both_classes():
    old = _fact("2017-08-10 10:00:00", "2017-08-10", 80, "prior", "2017-06-30")
    new = _fact("2017-11-09 19:02:00", "2017-11-13", 100, "0001193125-17-339122")
    panel = pd.DataFrame([_row(d, new, t) for d in ("2017-11-10", "2017-11-17") for t in ("NWS", "NWSA")])
    original = panel.copy(deep=True)
    result, changes, audit = reconcile_panel(panel, pd.DataFrame([old, new]))
    assert result.revenue.tolist() == [80, 80, 100, 100]
    assert result.revenue_adsh.tolist() == ["prior", "prior", new["adsh"], new["adsh"]]
    assert audit.empty and len(changes) > 0
    pd.testing.assert_frame_equal(panel, original)
    pd.testing.assert_series_equal(result.close, original.close)


def test_illumina_without_prior_fact_remains_missing():
    fact = _fact("2023-11-09 18:31:00", "2023-11-13", 100, "0001110803-23-000086")
    result, _, audit = reconcile_panel(pd.DataFrame([_row("2023-11-10", fact, "ILMN")]), pd.DataFrame([fact]))
    assert pd.isna(result.revenue.iloc[0]) and pd.isna(result.revenue_adsh.iloc[0])
    assert audit.empty


def test_annual_reselection_restores_filing_provenance():
    old = _fact("2016-11-01 10:00:00", "2016-11-01", 70, "annual_prior", "2016-06-30", 4)
    new = _fact("2017-11-09 19:02:00", "2017-11-13", 100, "annual_new", "2017-06-30", 4)
    panel = pd.DataFrame([_row("2017-11-10", new, annual=True)])
    result, _, audit = reconcile_panel(panel, pd.DataFrame([old, new]))
    assert result.annual_revenue.iloc[0] == 70
    assert str(result.annual_revenue_filed_date.iloc[0]) == "2016-11-01"
    assert result.annual_revenue_adsh.iloc[0] == "annual_prior"
    assert audit.empty


def test_replay_mismatch_fails_closed():
    fact = _fact("2017-11-09 19:02:00", "2017-11-13", 100, "new")
    row = _row("2017-11-10", fact)
    row["revenue"] = 999
    try:
        reconcile_panel(pd.DataFrame([row]), pd.DataFrame([fact]))
    except ValueError as error:
        assert "replay mismatch" in str(error)
    else:
        raise AssertionError("Mismatched cache was accepted")


def test_full_timestamp_cutoff_and_missing_provenance():
    fact = _fact("2017-11-10 16:00:01", "2017-11-10", 100, "same_day")
    panel = pd.DataFrame([_row("2017-11-10", fact)])
    audit = audit_panel_availability(panel)
    assert "accepted_after_cutoff" in set(audit.status)
    panel["revenue_accepted_at"] = ""
    assert "missing_or_invalid_acceptance" in set(audit_panel_availability(panel).status)
    panel["revenue_accepted_at"] = "2017-11-10 16:00:00"
    assert audit_panel_availability(panel).empty
    assert eastern_timestamp("2017-11-10 21:00:00+00:00") == eastern_timestamp("2017-11-10 16:00:00")
    assert eastern_timestamp("2017-07-10 20:00:00+00:00") == eastern_timestamp("2017-07-10 16:00:00")


def test_newer_available_period_beats_later_arriving_older_period():
    older = _fact("2017-11-09 19:02:00", "2017-11-13", 50, "older", "2017-06-30")
    newer = _fact("2017-11-10 09:00:00", "2017-11-10", 100, "newer")
    panel = pd.DataFrame([_row("2017-11-10", newer), _row("2017-11-17", newer)])
    result, _, audit = reconcile_panel(panel, pd.DataFrame([older, newer]))
    assert result.revenue.tolist() == [100, 100] and audit.empty
