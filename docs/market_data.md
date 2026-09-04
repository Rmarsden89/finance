# Market data source evaluation

## Objective

The backtest needs point-in-time daily market data for historical S&P 500
constituents, including securities that were later acquired, delisted, or failed.

Required fields for v0.1:

- date
- raw open/high/low/close
- volume
- adjusted close when available
- splits and dividends, either in the same source or a companion source

## Stooq: first free coverage test

Stooq is the first free provider adapter. It exposes long daily OHLCV histories
through CSV and uses the `.US` suffix for U.S. securities.

It is a coverage candidate, not yet an approved canonical price source.

Known limitations:

- API key is required for downloads.
- Exact free request quota is not published.
- The daily CSV endpoint does not expose explicit dividend/split events.
- Delisted-symbol retention must be measured; it must not be assumed.

Run the canary audit before a full-universe audit. The canary intentionally
contains active controls plus acquired, failed, and delisted historical names.

## Survivorship rule

A missing historical security must never be silently dropped from a backtest.
Price coverage is reported explicitly and can block a test window if coverage
falls below the eventual research threshold.

## Paid fallback under evaluation

EODHD explicitly documents continued EOD availability for delisted securities,
including acquired and bankrupt companies. Full historical depth is paid, so it
is a fallback only if free-source coverage proves insufficient.

A paid source should not be adopted until the free coverage audit quantifies the
actual gap.
