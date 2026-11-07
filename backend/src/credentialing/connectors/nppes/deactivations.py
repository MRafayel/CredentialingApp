"""The NPPES monthly deactivation file: one row per deactivated NPI, in an Excel workbook.

Observed 2026-11-06 (``NPPES_Deactivated_NPI_Report_110226_V2.zip``, 2,690,499 bytes): one
member ``NPPES Deactivated NPI Report 20261102.xlsx`` (5,662,312 bytes), one sheet
``DeactivatedNPIs``; row 1 is a title ("NPPES Deactivated Records as of ..."), row 2 the
header ``NPI | NPPES Deactivation Date``, then 360,014 rows, both cells text, the date
``MM/DD/YYYY``. openpyxl reads the whole sheet in about 2.2 s; it is read through
``framework/spreadsheet.py`` once per pass.

A row's line hash is the SHA-256 of ``deactivated|MM/DD/YYYY``, so an NPI deactivated again
on another date hashes differently; its record is a stub: the NPI, ``DEACTIVATED``, an
``UNKNOWN`` entity kind and the deactivation date.
"""

import hashlib
import re
from collections.abc import Iterator
from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path
from typing import Final
from uuid import UUID

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import parse_us_date
from credentialing.connectors.framework.zipped import member_name, open_archive, open_member
from credentialing.domain.enums import EntityKind, NpiStatus
from credentialing.domain.errors import DatasetFormatError
from credentialing.domain.models import NpiRecord

WORKBOOK_MEMBER: Final = re.compile(r"NPPES Deactivated NPI Report \d{8}\.xlsx")
"""The workbook inside the deactivation zip."""

NPI_COLUMN: Final = "NPI"
DATE_COLUMN: Final = "NPPES Deactivation Date"

_TEN_DIGITS: Final = re.compile(r"[0-9]{10}")
_FIRST_DATA_ROW: Final = 3
"""Rows are numbered as in the sheet: a title row and the header come first."""


class NppesDeactivationDataset:
    """Implements :class:`~credentialing.domain.ports.Dataset` over the deactivation workbook.

    The workbook (a few megabytes) is read into memory once; each pass reads its rows again.

    Raises:
        DatasetFormatError: The file is not a zip, has no (or several) workbooks, or the
            workbook has no ``NPI | NPPES Deactivation Date`` header.
    """

    def __init__(self, path: Path, snapshot_id: UUID, *, delete_on_close: bool = False) -> None:
        self._path = path
        self._snapshot_id = snapshot_id
        self._delete_on_close = delete_on_close
        with open_archive(path) as archive:
            name = member_name(archive, WORKBOOK_MEMBER)
            with open_member(archive, name) as stream:
                self._workbook = stream.read()
        next(self._rows(), None)

    def scan(self) -> Iterator[tuple[str, str]]:
        """Every row's NPI and line hash, in sheet order."""
        for _, npi, deactivated in self._rows():
            yield npi, _line_hash(deactivated)

    def records(self, npis: AbstractSet[str] | None) -> Iterator[NpiRecord]:
        """A deactivated stub for each row whose NPI is in ``npis`` (``None``: all)."""
        for _, npi, deactivated in self._rows():
            if npis is None or npi in npis:
                yield NpiRecord(
                    npi=npi,
                    entity_kind=EntityKind.UNKNOWN,
                    status=NpiStatus.DEACTIVATED,
                    deactivation_date=deactivated,
                    line_hash=_line_hash(deactivated),
                    snapshot_id=self._snapshot_id,
                )

    def close(self) -> None:
        """Drop the workbook, and delete the zip when the dataset was opened to be deleted."""
        self._workbook = b""
        if self._delete_on_close:
            self._path.unlink(missing_ok=True)

    def _rows(self) -> Iterator[tuple[int, str, date]]:
        rows = read_sheet(self._workbook, expected_columns=(NPI_COLUMN, DATE_COLUMN))
        try:
            for number, row in enumerate(rows, start=_FIRST_DATA_ROW):
                npi = row[NPI_COLUMN]
                if not _TEN_DIGITS.fullmatch(npi):
                    raise DatasetFormatError(f"row {number}: the NPI is not ten digits")
                yield number, npi, _deactivation_date(row[DATE_COLUMN], number)
        except ParseError as error:
            raise DatasetFormatError(str(error)) from None


def _deactivation_date(text: str, number: int) -> date:
    """The row's date: ``MM/DD/YYYY`` text, or an ISO date when the cell is a real date."""
    try:
        found = date.fromisoformat(text) if "-" in text else parse_us_date(text)
    except ParseError, ValueError:
        found = None
    if found is None:
        raise DatasetFormatError(f"row {number}: {DATE_COLUMN} is not a date in MM/DD/YYYY")
    return found


def _line_hash(deactivated: date) -> str:
    return hashlib.sha256(f"deactivated|{deactivated:%m/%d/%Y}".encode()).hexdigest()
