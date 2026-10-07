from pathlib import Path


def test_finalize_script_exists() -> None:
    assert Path('scripts/finalize_v5_v1_diagnostic.py').exists()
