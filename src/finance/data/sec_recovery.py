from __future__ import annotations

import pandas as pd


FAILURE_STATUSES = {"new_filing_partial", "submissions_error"}


def sec_status_counts(frame: pd.DataFrame) -> dict[str, int]:
    if "status" not in frame.columns:
        return {}
    counts = frame["status"].astype(str).value_counts(dropna=False)
    return {str(key): int(value) for key, value in counts.items()}


def failed_tickers(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "status" not in frame.columns or "ticker" not in frame.columns:
        return []
    mask = frame["status"].astype(str).isin(FAILURE_STATUSES)
    tickers = {
        str(value).strip().upper()
        for value in frame.loc[mask, "ticker"].tolist()
        if str(value).strip()
    }
    return sorted(tickers)


def merge_targeted_recovery(
    discovery: pd.DataFrame,
    recovery: pd.DataFrame,
) -> pd.DataFrame:
    """Replace discovery rows for retried tickers with targeted recovery rows."""
    retry_tickers = {
        str(value).strip().upper()
        for value in recovery.get("ticker", pd.Series(dtype="string")).tolist()
        if str(value).strip()
    }
    if not retry_tickers:
        return discovery.copy()

    base = discovery.copy()
    if "ticker" not in base.columns:
        raise ValueError("discovery frame is missing ticker")
    if "ticker" not in recovery.columns:
        raise ValueError("recovery frame is missing ticker")

    keep_mask = ~base["ticker"].astype(str).str.upper().isin(retry_tickers)
    merged = pd.concat([base.loc[keep_mask], recovery], ignore_index=True, sort=False)

    sort_columns = [
        column
        for column in ("ticker", "filing_date", "accession")
        if column in merged.columns
    ]
    if sort_columns:
        merged = merged.sort_values(sort_columns, kind="stable", na_position="last")
    return merged.reset_index(drop=True)
