"""Reading one member of a downloaded zip archive as a stream, chosen by its name.

Dataset files are published zipped with reference files and a readme beside the data (NPPES);
the member is picked by a pattern so a new date in its name does not break the reader, and
it is streamed, never extracted to disk.
"""

import re
import zipfile
from pathlib import Path
from typing import IO

from credentialing.domain.errors import DatasetFormatError


def open_archive(path: Path) -> zipfile.ZipFile:
    """Open a zip archive for reading.

    Raises:
        DatasetFormatError: The file is not a readable zip archive.
    """
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise DatasetFormatError(f"not a zip archive ({type(error).__name__})") from error


def member_name(archive: zipfile.ZipFile, pattern: re.Pattern[str]) -> str:
    """The name of the one member whose whole name ``pattern`` matches.

    Raises:
        DatasetFormatError: No member, or more than one, matches.
    """
    found = [name for name in archive.namelist() if pattern.fullmatch(name)]
    if len(found) != 1:
        raise DatasetFormatError(f"{len(found)} members match {pattern.pattern}, expected 1")
    return found[0]


def open_member(archive: zipfile.ZipFile, name: str) -> IO[bytes]:
    """The member as a binary stream, decompressed as it is read.

    Raises:
        DatasetFormatError: The member cannot be opened (corrupt or unsupported).
    """
    try:
        return archive.open(name)
    except (zipfile.BadZipFile, NotImplementedError, OSError) as error:
        raise DatasetFormatError(f"member {name} unreadable ({type(error).__name__})") from error
