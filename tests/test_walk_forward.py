from datetime import date

import pytest

from finance.backtest import expanding_walk_forward_windows


def test_expanding_walk_forward_windows() -> None:
    windows = expanding_walk_forward_windows(
        first_train_year=2015,
        first_test_year=2021,
        final_test_year=2023,
    )

    assert len(windows) == 3

    assert windows[0].train_start == date(2015, 1, 1)
    assert windows[0].train_end == date(2020, 12, 31)
    assert windows[0].test_start == date(2021, 1, 1)
    assert windows[0].test_end == date(2021, 12, 31)

    assert windows[-1].train_end == date(2022, 12, 31)
    assert windows[-1].test_start == date(2023, 1, 1)


def test_rejects_invalid_window_order() -> None:
    with pytest.raises(ValueError):
        expanding_walk_forward_windows(
            first_train_year=2021,
            first_test_year=2021,
            final_test_year=2023,
        )
