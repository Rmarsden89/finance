from pathlib import Path

from scripts.build_v5_attribution_closeout import sha256_file

def test_sha256_file_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / 'sample.txt'
    path.write_text('v5 attribution\n', encoding='utf-8')
    assert sha256_file(path) == sha256_file(path)
    assert len(sha256_file(path)) == 64
