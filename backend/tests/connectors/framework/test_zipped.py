import io
import re
import zipfile
from pathlib import Path

import pytest

from credentialing.connectors.framework.zipped import member_name, open_archive, open_member
from credentialing.domain.errors import DatasetFormatError

CSV = re.compile(r"data_\d{8}\.csv")


def _archive(tmp_path: Path, *names: str) -> Path:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr(name, f"contents of {name}\n")
    path = tmp_path / "file.zip"
    path.write_bytes(buffer.getvalue())
    return path


def test_the_one_matching_member_is_streamed(tmp_path: Path) -> None:
    path = _archive(tmp_path, "data_20260105.csv", "data_20260105_fileheader.csv", "readme.pdf")

    with open_archive(path) as archive:
        name = member_name(archive, CSV)
        with open_member(archive, name) as stream:
            assert stream.read() == b"contents of data_20260105.csv\n"

    assert name == "data_20260105.csv"


@pytest.mark.parametrize(
    ("names", "count"), [(("readme.pdf",), 0), (("data_20260105.csv", "data_20260201.csv"), 2)]
)
def test_no_member_or_several_is_refused(
    tmp_path: Path, names: tuple[str, ...], count: int
) -> None:
    with (
        open_archive(_archive(tmp_path, *names)) as archive,
        pytest.raises(DatasetFormatError, match=f"{count} members match"),
    ):
        member_name(archive, CSV)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "page.zip"
    path.write_bytes(b"<html>Not found.</html>")

    with pytest.raises(DatasetFormatError, match=r"not a zip archive \(BadZipFile\)"):
        open_archive(path)
