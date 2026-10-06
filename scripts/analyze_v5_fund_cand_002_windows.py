from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def classify_window(row: pd.Series) -> str:
    return "WIN" if float(row["xirr_delta"]) > 0 else "LOSS"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect V5-FUND-CAND-002 five-year robustness windows."
    )
    parser.add_argument(
        "--rolling",
        type=Path,
        default=Path(
            "reports/v5/fundamental/V5-FUND-CAND-002/rolling_windows.csv"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "reports/v5/fundamental/V5-FUND-CAND-002/"
            "five_year_window_diagnostic.csv"
        ),
    )
    args = parser.parse_args()

    frame = pd.read_csv(args.rolling)
    five = frame.loc[frame["window_years"].eq(5)].copy()
    if five.empty:
        raise SystemExit("No five-year windows found.")

    five["result_vs_v1"] = five.apply(classify_window, axis=1)
    five["candidate_minus_v1_xirr_pp"] = (
        pd.to_numeric(five["xirr_delta"], errors="coerce") * 100.0
    )
    five["candidate_minus_v1_terminal"] = (
        pd.to_numeric(five["candidate_terminal_value"], errors="coerce")
        - pd.to_numeric(five["v1_terminal_value"], errors="coerce")
    )

    five = five.sort_values(["start_year", "end_year"]).reset_index(drop=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    five.to_csv(args.output, index=False)

    print("V5 #47 CAND-002 FIVE-YEAR WINDOW DIAGNOSTIC")
    print("Windows:                    {}".format(len(five)))
    print("Wins / Losses:              {}/{}".format(
        int(five["result_vs_v1"].eq("WIN").sum()),
        int(five["result_vs_v1"].eq("LOSS").sum()),
    ))
    print("WINDOW RESULTS")
    for row in five.itertuples(index=False):
        print(
            "  {}-{} {:4s} XIRR delta={:+.4f}pp terminal={:+,.2f}".format(
                int(row.start_year),
                int(row.end_year),
                row.result_vs_v1,
                float(row.candidate_minus_v1_xirr_pp),
                float(row.candidate_minus_v1_terminal),
            )
        )
    print("Output:                     " + str(args.output))


if __name__ == "__main__":
    main()
