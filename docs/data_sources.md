# Free Data Strategy

## Identity rule

**SEC CIK is the canonical company identifier. Ticker is a time-bounded attribute.**

Raw historical membership records may temporarily have a missing CIK. They must remain explicitly unresolved until identity can be supported by source evidence. The backtest must never silently equate two records merely because their ticker text matches.

## Fundamentals

Primary source: SEC Financial Statement Data Sets / EDGAR.

The point-in-time availability boundary is the filing acceptance/availability date, not merely the financial period end date.

## Current security mappings

Primary source: SEC company_tickers.json / company_tickers_exchange.json.

These are current associations and are useful for enrichment and validation, but they do not by themselves establish historical ticker-validity intervals.

## Historical S&P 500 membership

Initial free-source approach: ingest a reconstructed point-in-time S&P 500 event/snapshot dataset into our own normalized interval schema.

Canonical membership fields:

    index_name
    cik
    ticker
    company_name
    start_date
    end_date
    source

end_date is exclusive; null means the interval is currently active.

Community/free membership data is provenance-bearing input, not unquestioned canonical truth. We validate member counts, identity coverage, overlaps, and later price/fundamental coverage before a date is eligible for walk-forward evaluation.

Initial reference/validation sources:

- arielNacamulli/pitindex: free point-in-time S&P 500 coverage from 2005 onward, reconstructed from historical snapshots plus later public change events.
- SEC company ticker association files for current identity enrichment.

## Prices

Price ingestion comes after the historical universe is built. We fetch only securities and date ranges actually required by the membership history.

This lets us quantify missing and delisted price coverage before choosing or paying for a provider.

## Required coverage checks

For every evaluation date report at least:

    index members
    CIK-resolved members
    members with SEC fundamentals
    members with usable price history
    fully usable members
    coverage percentage

Dates that fail an agreed coverage threshold should be flagged rather than silently backtested with a survivor-biased subset.
