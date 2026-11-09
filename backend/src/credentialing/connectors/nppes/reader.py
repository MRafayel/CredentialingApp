"""The NPPES main CSV, read in two passes straight from the zip (``npidata_pfile_*.csv``).

Observed 2026-11-06 in the November 2026 monthly file (``NPPES_Data_Dissemination_November
_2026_V2.zip``, 1,130,479,129 bytes): the member ``npidata_pfile_20050523-20261102.csv`` is
11,776,341,517 bytes (1,048,041,781 compressed), ASCII, LF line endings, every field
double-quoted, 330 columns (:data:`~credentialing.connectors.nppes.columns.HEADER`), the
header as its first line (also shipped alone as ``..._fileheader.csv``). 9,891,116 records,
one per physical line: no field holds a line break or a quote (quote parity never odd), no
NPI repeats, and every record starts with ``"`` and ten digits, so the NPI is read by
slicing on every row. Rows are not ordered by NPI. Entity type is ``1`` (7,540,866), ``2``
(1,990,237) or empty (360,013): an empty one is a deactivated NPI whose row carries only the
NPI and ``NPI Deactivation Date``, everything else blank. Rows with both a type and a
deactivation date have a later reactivation date (19,376; active again). Dates are
``MM/DD/YYYY``. ``NPI Deactivation Reason Code`` is empty on every row. 31 organizations have
no legal business name. 84 rows name a replacement NPI. About 4,700 addresses carry a
foreign province in the state column, which the mirror does not keep (not a U.S. state code);
12,501 addresses have a country code other than ``US``.

**Two passes.** :meth:`NppesCsvDataset.scan` yields each record's NPI and the SHA-256 of its
exact bytes (line ending stripped) without parsing a field; the loader compares the hashes
with the mirror's and asks :meth:`NppesCsvDataset.records` for the rows that differ, which
alone are split into fields and validated. A record is complete when its count of ``"`` is
even (CSV doubles embedded quotes), so a record whose quoted field spans lines is joined
before hashing; the real file never needed it, the rule stays as a safety net. A record
whose NPI cannot be read, whose quotes never close, or whose fields do not map raises
:class:`~credentialing.domain.errors.DatasetFormatError` naming the line and column, never a
value. Nothing is held in memory beyond one record and the caller's NPI set.

Measured 2026-11-06 on the real November 2026 file, first 500,000 records, Python 3.14 on the
owner's Mac, one thread (wall equals CPU time: the work is CPU-bound): :meth:`scan` 0.79 s
(about 630,000 rows/s; the whole file about 16 s), :meth:`records` of every row 6.8 s (about
73,000 rows/s: CSV split about a third, mapping and pydantic validation the rest; every row
about 135 s), records plus the loader's COPY formatting 10.3 s (about 48,700 rows/s, 658 bytes
of COPY text per row). Peak memory 70 MB. Iterating the member through a 1 MiB
``BufferedReader`` reads lines about 2.7 times faster than iterating the zip member itself.
"""

import csv
import hashlib
import io
import re
from collections.abc import Iterable, Iterator, Sequence
from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path
from typing import Final, cast
from uuid import UUID

from pydantic import ValidationError

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.values import parse_us_date
from credentialing.connectors.framework.zipped import member_name, open_archive, open_member
from credentialing.connectors.nppes import columns as c
from credentialing.domain.enums import EntityKind, NpiStatus
from credentialing.domain.errors import DatasetFormatError
from credentialing.domain.models import NpiRecord

MAIN_MEMBER: Final = re.compile(r"npidata_pfile_\d{8}-\d{8}\.csv")
"""The main CSV inside a monthly or weekly zip; the ``_fileheader.csv`` and the reference files
(other names, practice locations, endpoints) do not match."""

_READ_BUFFER: Final = 1024 * 1024
_QUOTE: Final = 0x22
_ENTITY_KINDS: Final = {"1": EntityKind.INDIVIDUAL, "2": EntityKind.ORGANIZATION}
_FLAGS: Final = {"Y": True, "N": False}


class NppesCsvDataset:
    """Implements :class:`~credentialing.domain.ports.Dataset` over the main CSV of an NPPES
    monthly or weekly zip.

    Opening checks the archive, the member and the header; each pass then streams the member
    again. ``delete_on_close`` removes the zip when the loader is done with it.

    Raises:
        DatasetFormatError: The file is not a zip, has no (or several) main CSVs, or its header
            lacks a column the reader maps.
    """

    def __init__(
        self,
        path: Path,
        snapshot_id: UUID,
        *,
        member: re.Pattern[str] = MAIN_MEMBER,
        delete_on_close: bool = False,
    ) -> None:
        self._path = path
        self._snapshot_id = snapshot_id
        self._delete_on_close = delete_on_close
        self._archive = open_archive(path)
        try:
            self._member = member_name(self._archive, member)
            with open_member(self._archive, self._member) as stream:
                first = stream.readline()
            header = _fields(first.rstrip(b"\r\n"), 1)
            self._mapper = _Mapper(header, snapshot_id)
        except BaseException:
            self._archive.close()
            raise

    def scan(self) -> Iterator[tuple[str, str]]:
        """Every record's NPI and line hash, in file order, without parsing its fields."""
        npi_of = self._mapper.npi
        sha256 = hashlib.sha256
        for number, record in self._records():
            yield npi_of(record, number), sha256(record).hexdigest()

    def records(self, npis: AbstractSet[str] | None) -> Iterator[NpiRecord]:
        """The records whose NPI is in ``npis`` (``None``: all), in file order; only those rows
        are split into fields."""
        mapper = self._mapper
        current: list[tuple[int, str]] = [(0, "")]

        def selected() -> Iterator[str]:
            for number, record in self._records():
                if npis is None or mapper.npi(record, number) in npis:
                    current[0] = (number, hashlib.sha256(record).hexdigest())
                    yield _decode(record, number)

        try:
            for fields in csv.reader(selected(), strict=True):
                number, digest = current[0]
                yield mapper.record(fields, digest, number)
        except csv.Error:
            raise DatasetFormatError(f"line {current[0][0]}: not a CSV record") from None

    def close(self) -> None:
        """Close the archive, and delete it when the dataset was opened to be deleted."""
        self._archive.close()
        if self._delete_on_close:
            self._path.unlink(missing_ok=True)

    def _records(self) -> Iterator[tuple[int, bytes]]:
        """Each record as its first line number and its exact bytes, line ending stripped.

        A line with an odd number of quotes opens a quoted field; the following lines are
        joined to it until the count is even.
        """
        with open_member(self._archive, self._member) as raw:
            # A large buffer reads lines in C: about 2.7 times faster than iterating the member.
            stream = io.BufferedReader(cast("io.RawIOBase", raw), _READ_BUFFER)
            stream.readline()
            number = 1
            pending: list[bytes] = []
            start = 0
            for line in stream:
                number += 1
                odd = line.count(_QUOTE) & 1
                if pending:
                    pending.append(line)
                    if odd:
                        yield start, b"".join(pending).rstrip(b"\r\n")
                        pending = []
                    continue
                if odd:
                    pending.append(line)
                    start = number
                    continue
                yield number, line.rstrip(b"\r\n")
            if pending:
                raise DatasetFormatError(f"line {start}: a quoted field never closes")


class _Mapper:
    """Maps one record's fields onto an :class:`NpiRecord`, by column positions found once."""

    def __init__(self, header: Sequence[str], snapshot_id: UUID) -> None:
        index = c.header_index(header)
        self._names = tuple(header)
        self._width = len(header)
        self._snapshot_id = snapshot_id
        self._npi_first = index[c.NPI] == 0

        def at(name: str) -> int:
            return index[name]

        self._npi = at(c.NPI)
        self._entity = at(c.ENTITY_TYPE)
        self._replacement = at(c.REPLACEMENT_NPI)
        self._organization = at(c.ORGANIZATION_NAME)
        self._person = (at(c.LAST_NAME), at(c.FIRST_NAME), at(c.MIDDLE_NAME), at(c.NAME_SUFFIX))
        self._prefix = at(c.NAME_PREFIX)
        self._credential = at(c.CREDENTIAL)
        self._other_organization = at(c.OTHER_ORGANIZATION_NAME)
        self._other_person = (
            at(c.OTHER_LAST_NAME),
            at(c.OTHER_FIRST_NAME),
            at(c.OTHER_MIDDLE_NAME),
        )
        self._parent = at(c.PARENT_ORGANIZATION_NAME)
        self._mailing = tuple(
            at(name)
            for name in (
                c.MAILING_LINE1,
                c.MAILING_LINE2,
                c.MAILING_CITY,
                c.MAILING_STATE,
                c.MAILING_POSTAL_CODE,
                c.MAILING_COUNTRY,
            )
        )
        self._practice = tuple(
            at(name)
            for name in (
                c.PRACTICE_LINE1,
                c.PRACTICE_LINE2,
                c.PRACTICE_CITY,
                c.PRACTICE_STATE,
                c.PRACTICE_POSTAL_CODE,
                c.PRACTICE_COUNTRY,
            )
        )
        self._enumerated = at(c.ENUMERATION_DATE)
        self._updated = at(c.LAST_UPDATE_DATE)
        self._certified = at(c.CERTIFICATION_DATE)
        self._reason = at(c.DEACTIVATION_REASON)
        self._deactivated = at(c.DEACTIVATION_DATE)
        self._reactivated = at(c.REACTIVATION_DATE)
        self._official = (
            at(c.OFFICIAL_LAST_NAME),
            at(c.OFFICIAL_FIRST_NAME),
            at(c.OFFICIAL_MIDDLE_NAME),
            at(c.OFFICIAL_SUFFIX),
        )
        self._official_extra = (
            at(c.OFFICIAL_PREFIX),
            at(c.OFFICIAL_TITLE),
            at(c.OFFICIAL_CREDENTIAL),
        )
        self._sole = at(c.SOLE_PROPRIETOR)
        self._subpart = at(c.ORGANIZATION_SUBPART)
        self._taxonomies = tuple(
            (
                at(c.taxonomy_code(group)),
                at(c.license_number(group)),
                at(c.license_state(group)),
                at(c.primary_switch(group)),
                at(c.taxonomy_group(group)),
            )
            for group in range(1, c.TAXONOMY_GROUPS + 1)
        )

    def npi(self, record: bytes, number: int) -> str:
        """The record's NPI: sliced from ``"0123456789",`` when it is the first column, else
        read from its parsed fields.

        Raises:
            DatasetFormatError: The NPI column does not hold ten digits.
        """
        if self._npi_first and record[11:12] == b'"' and record[:1] == b'"':
            digits = record[1:11]
            if digits.isdigit():
                return digits.decode("ascii")
        fields = _fields(record, number)
        value = fields[self._npi] if len(fields) > self._npi else ""
        if len(value) != 10 or not value.isascii() or not value.isdigit():
            raise DatasetFormatError(f"line {number}: the NPI is not ten digits")
        return value

    def record(self, f: Sequence[str], line_hash: str, number: int) -> NpiRecord:
        """The record of one row's fields."""
        if len(f) != self._width:
            raise DatasetFormatError(f"line {number}: {len(f)} columns, expected {self._width}")
        deactivated = self._date(f, self._deactivated, number)
        reactivated = self._date(f, self._reactivated, number)
        status = (
            NpiStatus.DEACTIVATED
            if deactivated is not None and (reactivated is None or reactivated < deactivated)
            else NpiStatus.ACTIVE
        )
        code = f[self._entity]
        if code in _ENTITY_KINDS:
            kind = _ENTITY_KINDS[code]
        elif not code and deactivated is not None:
            kind = EntityKind.UNKNOWN
        else:
            raise DatasetFormatError(
                f"line {number}: {c.ENTITY_TYPE} is not 1, 2 or a stub's blank"
            )

        person: dict[str, str | None] | None = None
        organization: str | None = None
        if kind is EntityKind.INDIVIDUAL:
            last, first, middle, suffix = (f[i] for i in self._person)
            if last or first:
                person = {
                    "last": last,
                    "first": first,
                    "middle": middle or None,
                    "suffix": suffix or None,
                }
        elif kind is EntityKind.ORGANIZATION:
            organization = f[self._organization] or None

        other_names = unique_names(
            (
                f[self._other_organization],
                other_person_name(*(f[i] for i in self._other_person)),
                f[self._parent],
            )
        )

        official: dict[str, object] | None = None
        official_last, official_first, official_middle, official_suffix = (
            f[i] for i in self._official
        )
        if official_last or official_first:
            prefix, title, credential = (f[i] for i in self._official_extra)
            official = {
                "name": {
                    "last": official_last,
                    "first": official_first,
                    "middle": official_middle or None,
                    "suffix": official_suffix or None,
                },
                "prefix": prefix or None,
                "title": title or None,
                "credential": credential or None,
            }

        taxonomies = [
            {
                "code": f[code_at],
                "primary": f[switch_at] == "Y",
                "license_number": f[number_at] or None,
                "license_state": state_code(f[state_at]),
                "group": f[group_at] or None,
            }
            for code_at, number_at, state_at, switch_at, group_at in self._taxonomies
            if f[code_at]
        ]
        data = {
            "npi": f[self._npi],
            "entity_kind": kind,
            "status": status,
            "replacement_npi": f[self._replacement] or None,
            "person_name": person,
            "name_prefix": (f[self._prefix] or None) if person else None,
            "credential": f[self._credential] or None,
            "organization_name": organization,
            "other_names": other_names,
            "sole_proprietor": _FLAGS.get(f[self._sole]),
            "organization_subpart": _FLAGS.get(f[self._subpart]),
            "enumeration_date": self._date(f, self._enumerated, number),
            "last_updated": self._date(f, self._updated, number),
            "certification_date": self._date(f, self._certified, number),
            "deactivation_date": deactivated,
            "deactivation_reason": f[self._reason] or None,
            "reactivation_date": reactivated,
            "mailing_address": _address(f, self._mailing),
            "practice_address": _address(f, self._practice),
            "taxonomies": taxonomies,
            "authorized_official": official,
            "line_hash": line_hash,
            "snapshot_id": self._snapshot_id,
        }
        try:
            return NpiRecord.model_validate(data)
        except ValidationError as error:
            fields = sorted(
                {
                    str(e["loc"][0]) if e["loc"] else "record"
                    for e in error.errors(include_input=False)
                }
            )
            raise DatasetFormatError(
                f"line {number}: the record does not validate ({', '.join(fields)})"
            ) from None

    def _date(self, f: Sequence[str], at: int, number: int) -> date | None:
        try:
            return parse_us_date(f[at])
        except ParseError:
            raise DatasetFormatError(
                f"line {number}: {self._names[at]} is not a date in MM/DD/YYYY"
            ) from None


def _address(f: Sequence[str], at: tuple[int, ...]) -> dict[str, str | None] | None:
    """An address from its six columns (:func:`address_fields`)."""
    return address_fields(*(f[i] for i in at))


def address_fields(
    line1: str, line2: str, city: str, state: str, postal_code: str, country: str
) -> dict[str, str | None] | None:
    """An address's fields as the mirror keeps them; ``None`` when all are empty. Kept as
    published, the state only when it is a two-letter code. Shared with the API mapping."""
    if not (line1 or line2 or city or state or postal_code or country):
        return None
    return {
        "line1": line1 or None,
        "line2": line2 or None,
        "city": city or None,
        "state": state_code(state),
        "postal_code": postal_code or None,
        "country": country or None,
    }


def state_code(value: str) -> str | None:
    """``value`` when it is a two-letter code in capitals (a U.S. state), else ``None``."""
    return (
        value
        if len(value) == 2 and value.isascii() and value.isalpha() and value.isupper()
        else None
    )


def other_person_name(last: str, first: str, middle: str) -> str | None:
    """A person's other name as the mirror keeps it: "Last, First Middle"; ``None`` without a
    last name."""
    if not last:
        return None
    given = " ".join(part for part in (first, middle) if part)
    return f"{last}, {given}" if given else last


def unique_names(names: Iterable[str | None]) -> tuple[str, ...]:
    """The names that are filled in, without repeats, in order."""
    return tuple(dict.fromkeys(name for name in names if name))


def _decode(record: bytes, number: int) -> str:
    try:
        return record.decode("utf-8")
    except UnicodeDecodeError:
        raise DatasetFormatError(f"line {number}: not UTF-8 text") from None


def _fields(record: bytes, number: int) -> list[str]:
    """One record split into fields (the header, or a row whose NPI cannot be sliced)."""
    try:
        return next(csv.reader([_decode(record, number)], strict=True), [])
    except csv.Error:
        raise DatasetFormatError(f"line {number}: not a CSV record") from None
