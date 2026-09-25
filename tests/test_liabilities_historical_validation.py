from pathlib import Path
from zipfile import ZipFile

from finance.research.liabilities_historical_validation import (
    build_quarter_liabilities_comparisons,
)


def _write_zip(path: Path, *, current: int, noncurrent: int, direct: int) -> None:
    sub = (
        "adsh\tcik\tform\tperiod\tfiled\taccepted\n"
        "0001\t123\t10-Q\t20200630\t20200730\t20200730120000\n"
    )
    num = (
        "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\n"
        f"0001\tLiabilitiesCurrent\tus-gaap/2020\t20200630\t0\tUSD\t\t\t{current}\n"
        f"0001\tLiabilitiesNoncurrent\tus-gaap/2020\t20200630\t0\tUSD\t\t\t{noncurrent}\n"
        f"0001\tLiabilities\tus-gaap/2020\t20200630\t0\tUSD\t\t\t{direct}\n"
    )
    pre = (
        "adsh\treport\tline\tstmt\ttag\tversion\n"
        "0001\t1\t1\tBS\tLiabilities\tus-gaap/2020\n"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("sub.txt", sub)
        archive.writestr("num.txt", num)
        archive.writestr("pre.txt", pre)


def test_historical_liabilities_construction_exact(tmp_path: Path):
    path = tmp_path / "2020q2.zip"
    _write_zip(path, current=400, noncurrent=600, direct=1000)

    candidates, comparable = build_quarter_liabilities_comparisons(path)

    assert len(candidates) == 1
    assert bool(candidates.iloc[0]["has_construction"])
    assert len(comparable) == 1
    assert comparable.iloc[0]["constructed_liabilities"] == 1000
    assert comparable.iloc[0]["validation_band"] == "exact_match"


def test_historical_liabilities_construction_flags_material_difference(
    tmp_path: Path,
):
    path = tmp_path / "2020q2.zip"
    _write_zip(path, current=400, noncurrent=500, direct=1000)

    _, comparable = build_quarter_liabilities_comparisons(path)

    assert len(comparable) == 1
    assert comparable.iloc[0]["validation_band"] == "material_difference"
    assert comparable.iloc[0]["absolute_relative_error"] == 0.1
