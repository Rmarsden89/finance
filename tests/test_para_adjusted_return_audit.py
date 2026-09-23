from __future__ import annotations

import pandas as pd


def test_para_disposition_logic_distinguishes_adjusted_only_from_raw() -> None:
    frame = pd.DataFrame([
        {
            "raw_return": 0.02,
            "adjusted_return": 0.95,
        },
        {
            "raw_return": 0.80,
            "adjusted_return": 0.90,
        },
    ])
    frame["raw_extreme"] = frame["raw_return"].abs().gt(0.75)
    frame["adjusted_extreme"] = frame["adjusted_return"].abs().gt(0.75)
    frame["disposition"] = frame.apply(
        lambda row: (
            "adjusted_only_discontinuity"
            if bool(row["adjusted_extreme"]) and not bool(row["raw_extreme"])
            else "raw_and_adjusted_discontinuity"
            if bool(row["adjusted_extreme"]) and bool(row["raw_extreme"])
            else "not_reproduced"
        ),
        axis=1,
    )

    assert frame["disposition"].tolist() == [
        "adjusted_only_discontinuity",
        "raw_and_adjusted_discontinuity",
    ]
