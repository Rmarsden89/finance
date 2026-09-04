from pathlib import Path

import pandas as pd

from finance.data.sec_multi_quarter import build_multi_quarter_winner_facts


def test_empty_multi_quarter_input() -> None:
    frame, audit = build_multi_quarter_winner_facts([])

    assert frame.empty
    assert audit.zip_count == 0
    assert audit.winner_rows_after_dedup == 0
