from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd


NCI_EQUITY = (
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
)
PLAIN_EQUITY = "StockholdersEquity"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse historical liabilities identity corroboration to one "
            "filing-level result per issuer/accession/period using a strict "
            "equity preference and without double-counting equivalent totals."
        )
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args()


def _pick_one_filing(group: pd.DataFrame) -> pd.Series:
    frame = group.copy()

    # Prefer the equity concept that includes NCI whenever available.
    if frame["equity_tag"].astype(str).eq(NCI_EQUITY).any():
        frame = frame.loc[
            frame["equity_tag"].astype(str).eq(NCI_EQUITY)
        ].copy()
        equity_preference = "nci_inclusive"
    else:
        frame = frame.loc[
            frame["equity_tag"].astype(str).eq(PLAIN_EQUITY)
        ].copy()
        equity_preference = "plain_stockholders_equity"

    if frame.empty:
        # Defensive fallback: preserve evidence rather than silently dropping it.
        frame = group.copy()
        equity_preference = "other_equity_tag"

    # Prefer Assets - Equity when both balance-sheet total paths exist.
    # LiabilitiesAndStockholdersEquity is commonly algebraically equivalent to
    # Assets; keeping only one avoids double-counting one accounting identity.
    if frame["identity_type"].astype(str).eq("assets_minus_equity").any():
        frame = frame.loc[
            frame["identity_type"].astype(str).eq("assets_minus_equity")
        ].copy()
        identity_preference = "assets_minus_equity"
    elif frame["identity_type"].astype(str).eq(
        "total_like_minus_equity"
    ).any():
        frame = frame.loc[
            frame["identity_type"].astype(str).eq(
                "total_like_minus_equity"
            )
        ].copy()
        identity_preference = "total_like_minus_equity"
    else:
        identity_preference = "other"

    # If duplicates remain under the same preferred identity, keep the row with
    # the lowest relative error only when all rows resolve to the same
    # corroborated value. Otherwise mark the filing ambiguous and fail closed.
    values = pd.to_numeric(
        frame["corroborated_liabilities"], errors="coerce"
    ).dropna()
    unique_values = sorted({float(v) for v in values})
    ambiguous = len(unique_values) != 1

    if ambiguous:
        selected = frame.iloc[0].copy()
        selected["collapsed_status"] = "ambiguous_corroboration"
        selected["collapsed_validation_band"] = "ambiguous"
        selected["equity_preference"] = equity_preference
        selected["identity_preference"] = identity_preference
        selected["duplicate_rows_collapsed"] = len(group)
        return selected

    frame = frame.sort_values(
        ["absolute_relative_error", "identity_type", "equity_tag"],
        kind="stable",
    )
    selected = frame.iloc[0].copy()
    selected["collapsed_status"] = "selected"
    selected["collapsed_validation_band"] = selected["validation_band"]
    selected["equity_preference"] = equity_preference
    selected["identity_preference"] = identity_preference
    selected["duplicate_rows_collapsed"] = len(group)
    return selected


def main() -> None:
    args = parse_args()
    root = args.repo_root.resolve()
    base = (
        root
        / "reports"
        / "v3"
        / "data_sources"
        / args.as_of.isoformat()
        / "liabilities_historical_validation"
    )
    source_path = base / "current_candidate_identity_corroboration.csv"
    if not source_path.exists():
        raise SystemExit(f"Missing corroboration detail: {source_path}")

    frame = pd.read_csv(source_path, low_memory=False)
    required = {
        "ticker",
        "cik",
        "adsh",
        "ddate_date",
        "equity_tag",
        "identity_type",
        "corroborated_liabilities",
        "absolute_relative_error",
        "validation_band",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(
            "Corroboration detail missing required columns: "
            + ", ".join(missing)
        )

    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame["cik"] = pd.to_numeric(frame["cik"], errors="coerce").astype("Int64")

    keys = ["ticker", "cik", "adsh", "ddate_date"]
    collapsed = (
        frame.groupby(keys, dropna=False, group_keys=False)
        .apply(_pick_one_filing, include_groups=False)
        .reset_index()
    )

    bands = collapsed["collapsed_validation_band"].astype(str)
    collapsed["historically_material"] = bands.eq("material_difference")
    collapsed["historically_clean"] = bands.isin(
        {"exact_match", "within_0_01_pct", "within_0_1_pct", "within_1_pct"}
    )
    collapsed["historically_ambiguous"] = bands.eq("ambiguous")

    by_ticker = (
        collapsed.groupby(["ticker", "cik"], as_index=False)
        .agg(
            filing_rows=("adsh", "size"),
            exact_matches=(
                "collapsed_validation_band",
                lambda s: int(s.eq("exact_match").sum()),
            ),
            within_0_01_pct=(
                "collapsed_validation_band",
                lambda s: int(s.eq("within_0_01_pct").sum()),
            ),
            within_0_1_pct=(
                "collapsed_validation_band",
                lambda s: int(s.eq("within_0_1_pct").sum()),
            ),
            within_1_pct=(
                "collapsed_validation_band",
                lambda s: int(s.eq("within_1_pct").sum()),
            ),
            material_rows=("historically_material", "sum"),
            ambiguous_rows=("historically_ambiguous", "sum"),
            max_absolute_relative_error=("absolute_relative_error", "max"),
        )
    )
    by_ticker["candidate_classification"] = "historically_corroborated_clean"
    by_ticker.loc[
        by_ticker["material_rows"].gt(0),
        "candidate_classification",
    ] = "historically_material"
    by_ticker.loc[
        by_ticker["material_rows"].eq(0)
        & by_ticker["ambiguous_rows"].gt(0),
        "candidate_classification",
    ] = "historically_ambiguous"

    summary = pd.DataFrame(
        [
            {
                "candidate_rows": int(len(by_ticker)),
                "collapsed_filing_rows": int(len(collapsed)),
                "historically_corroborated_clean": int(
                    by_ticker["candidate_classification"]
                    .eq("historically_corroborated_clean")
                    .sum()
                ),
                "historically_material": int(
                    by_ticker["candidate_classification"]
                    .eq("historically_material")
                    .sum()
                ),
                "historically_ambiguous": int(
                    by_ticker["candidate_classification"]
                    .eq("historically_ambiguous")
                    .sum()
                ),
                "material_filing_rows": int(
                    collapsed["historically_material"].sum()
                ),
                "ambiguous_filing_rows": int(
                    collapsed["historically_ambiguous"].sum()
                ),
                "nci_preferred_filing_rows": int(
                    collapsed["equity_preference"].eq("nci_inclusive").sum()
                ),
                "assets_identity_filing_rows": int(
                    collapsed["identity_preference"]
                    .eq("assets_minus_equity")
                    .sum()
                ),
                "research_only": True,
                "model_inputs_modified": False,
            }
        ]
    )

    detail_path = base / "current_candidate_identity_collapsed.csv"
    ticker_path = base / "current_candidate_identity_collapsed_by_ticker.csv"
    summary_path = base / "current_candidate_identity_collapsed_summary.csv"

    collapsed.to_csv(detail_path, index=False)
    by_ticker.to_csv(ticker_path, index=False)
    summary.to_csv(summary_path, index=False)

    row = summary.iloc[0]
    print("V3 CURRENT-CANDIDATE COLLAPSED IDENTITY CORROBORATION")
    print(f"As of:                       {args.as_of.isoformat()}")
    print(f"Candidates evaluated:        {int(row['candidate_rows'])}")
    print(f"Collapsed filing rows:       {int(row['collapsed_filing_rows'])}")
    print(
        f"Historically clean:          "
        f"{int(row['historically_corroborated_clean'])}"
    )
    print(
        f"Historically material:       "
        f"{int(row['historically_material'])}"
    )
    print(
        f"Historically ambiguous:      "
        f"{int(row['historically_ambiguous'])}"
    )
    print(
        f"Material filing rows:        "
        f"{int(row['material_filing_rows'])}"
    )
    print(
        f"Ambiguous filing rows:       "
        f"{int(row['ambiguous_filing_rows'])}"
    )
    print(
        f"NCI-preferred filings:       "
        f"{int(row['nci_preferred_filing_rows'])}"
    )
    print(f"Detail:                      {detail_path}")
    print(f"By ticker:                   {ticker_path}")
    print(f"Summary:                     {summary_path}")
    print("IDENTITY FACTS REMAIN VALIDATION-ONLY.")
    print("NO MODEL INPUTS WERE MODIFIED.")


if __name__ == "__main__":
    main()
