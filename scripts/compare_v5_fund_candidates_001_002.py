from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare CAND-001 and CAND-002 five-year windows."
    )
    parser.add_argument(
        "--cand1",
        type=Path,
        default=Path("reports/v5/fundamental/V5-FUND-CAND-001/rolling_windows.csv"),
    )
    parser.add_argument(
        "--cand2",
        type=Path,
        default=Path("reports/v5/fundamental/V5-FUND-CAND-002/rolling_windows.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reports/v5/fundamental/candidate_comparison/"
            "cand001_vs_cand002_five_year.csv"
        ),
    )
    args = parser.parse_args()

    a = pd.read_csv(args.cand1)
    b = pd.read_csv(args.cand2)

    a = a.loc[a["window_years"].eq(5)].copy()
    b = b.loc[b["window_years"].eq(5)].copy()

    keep = [
        "start_year",
        "end_year",
        "v1_xirr",
        "candidate_xirr",
        "xirr_delta",
        "v1_terminal_value",
        "candidate_terminal_value",
        "terminal_value_delta",
    ]
    a = a[keep].rename(columns={
        "candidate_xirr": "cand1_xirr",
        "xirr_delta": "cand1_xirr_delta",
        "candidate_terminal_value": "cand1_terminal_value",
        "terminal_value_delta": "cand1_terminal_delta",
    })
    b = b[keep].rename(columns={
        "candidate_xirr": "cand2_xirr",
        "xirr_delta": "cand2_xirr_delta",
        "candidate_terminal_value": "cand2_terminal_value",
        "terminal_value_delta": "cand2_terminal_delta",
    })

    merged = a.merge(
        b.drop(columns=["v1_xirr", "v1_terminal_value"]),
        on=["start_year", "end_year"],
        how="inner",
        validate="one_to_one",
    )
    merged["cand1_result"] = merged["cand1_xirr_delta"].gt(0).map({True:"WIN",False:"LOSS"})
    merged["cand2_result"] = merged["cand2_xirr_delta"].gt(0).map({True:"WIN",False:"LOSS"})
    merged["modifier_xirr_effect"] = merged["cand2_xirr"] - merged["cand1_xirr"]
    merged["modifier_terminal_effect"] = (
        merged["cand2_terminal_value"] - merged["cand1_terminal_value"]
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.output, index=False)

    print("V5 #47 CAND-001 VS CAND-002 FIVE-YEAR COMPARISON")
    print("WINDOW RESULTS")
    for row in merged.itertuples(index=False):
        print(
            "  {}-{} C1={} {:+.4f}pp C2={} {:+.4f}pp modifier={:+.4f}pp".format(
                int(row.start_year),
                int(row.end_year),
                row.cand1_result,
                float(row.cand1_xirr_delta) * 100.0,
                row.cand2_result,
                float(row.cand2_xirr_delta) * 100.0,
                float(row.modifier_xirr_effect) * 100.0,
            )
        )

    flipped = merged.loc[merged["cand1_result"].ne(merged["cand2_result"])]
    print("Result flips:               {}".format(len(flipped)))
    for row in flipped.itertuples(index=False):
        print(
            "  {}-{} {} -> {}".format(
                int(row.start_year),
                int(row.end_year),
                row.cand1_result,
                row.cand2_result,
            )
        )
    print("Output:                     " + str(args.output))


if __name__ == "__main__":
    main()
