from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WalkForwardWindow:
    """One expanding-window train/test split.

    Dates are expressed at the calendar-year level for the initial framework.
    Point-in-time data availability rules will be enforced by the data layer.
    """

    train_start: date
    train_end: date
    test_start: date
    test_end: date


def expanding_walk_forward_windows(
    *,
    first_train_year: int,
    first_test_year: int,
    final_test_year: int,
) -> list[WalkForwardWindow]:
    """Build expanding annual train/test windows.

    Example:
        train 2015-2020 -> test 2021
        train 2015-2021 -> test 2022
        ...

    This function deliberately does not score securities or load data. It only
    defines the temporal experiment boundary so later components cannot blur
    training and evaluation periods.
    """

    if first_train_year >= first_test_year:
        raise ValueError("first_train_year must be earlier than first_test_year")
    if final_test_year < first_test_year:
        raise ValueError("final_test_year must be >= first_test_year")

    windows: list[WalkForwardWindow] = []

    for test_year in range(first_test_year, final_test_year + 1):
        windows.append(
            WalkForwardWindow(
                train_start=date(first_train_year, 1, 1),
                train_end=date(test_year - 1, 12, 31),
                test_start=date(test_year, 1, 1),
                test_end=date(test_year, 12, 31),
            )
        )

    return windows
