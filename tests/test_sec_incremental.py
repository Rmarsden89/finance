from pathlib import Path

from finance.data.sec_incremental import materialize_sec_quarters_incrementally


def test_incremental_empty_input(tmp_path: Path) -> None:
    frame, audit = materialize_sec_quarters_incrementally(
        [],
        quarter_cache_dir=tmp_path / "cache",
    )

    assert frame.empty
    assert audit.zip_count == 0
    assert audit.processed_quarters == 0
    assert audit.reused_quarters == 0
