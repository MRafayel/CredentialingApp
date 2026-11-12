"""Use case: add many providers to an organization's roster from a pasted list or a CSV export.

The text is read as CSV, TSV or another delimited export, its columns recognized by their
headers (:data:`HEADER_SYNONYMS`), and every row turned into a provider through the rules of
:meth:`RegisterProvider.build`, with the problems found on the way: an *error* keeps the row
out, a *warning* drops one value and keeps the row. A dry run returns that preview; a commit
stores the importable rows at once and runs their first check
(:meth:`RecheckProviders.run_for`), so the roster answers right away.

Duplicates are refused: within the text (by NPI, else by name and date of birth) and against
the caller's own roster, read once; another organization's roster is never read. A roster
holds each NPI once in the database too: when another commit stored one of the file's NPIs
after that read (two commits of one file at once), the store raises
:class:`~credentialing.domain.errors.DuplicateNpiError` and nothing of this commit is stored.
The text is never stored and no cell is ever logged. Each row hands its recognized cells back
(:attr:`ImportRow.values`) so the preview can be edited and read again: a column's own name
(:class:`ImportColumn`) is always a header, so text rebuilt from ``columns`` and ``values``
reads the same.
"""

import csv
import io
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import InvalidProviderInputError
from credentialing.application.recheck_providers import IMPORT_TRIGGER, FirstChecking
from credentialing.application.register_provider import ProviderInput, RegisterProvider
from credentialing.domain.enums import EntityKind, RosterRole
from credentialing.domain.models import License, MonitoringRun, PersonName, Provider
from credentialing.domain.names import (
    CREDENTIAL_SUFFIXES,
    normalize_name,
    normalize_organization_name,
)
from credentialing.domain.ports import Clock, ProviderRepository
from credentialing.domain.types import validate_npi

logger = logging.getLogger(__name__)

MAX_BYTES: Final = 2 * 1024 * 1024
"""The largest text accepted, in UTF-8 bytes."""

MAX_ROWS: Final = 5_000
"""The most data rows accepted (blank rows not counted)."""

_SNIFF_LINES: Final = 20
_DELIMITERS: Final = ",\t;|"


class ImportColumn(StrEnum):
    """A column of an import the rows are read from. ``NAME`` is a whole name in one cell,
    split into its parts."""

    LAST = "last"
    FIRST = "first"
    MIDDLE = "middle"
    SUFFIX = "suffix"
    NAME = "name"
    ORGANIZATION = "organization"
    NPI = "npi"
    DATE_OF_BIRTH = "date_of_birth"
    LICENSE_NUMBER = "license_number"
    LICENSE_STATE = "license_state"
    ROLE = "role"


class ImportSeverity(StrEnum):
    """An ``error`` keeps the row out of the import; a ``warning`` drops one value only."""

    ERROR = "error"
    WARNING = "warning"


class ImportProblemCode(StrEnum):
    """What is wrong with a row; each code has one severity (:data:`SEVERITY`)."""

    NAME_MISSING = "name_missing"
    """An individual without a last or a first name, or a row with no name at all."""
    NPI_INVALID = "npi_invalid"
    """Not ten digits, or the check digit is wrong."""
    DUPLICATE_IN_FILE = "duplicate_in_file"
    """The same NPI, or the same name and date of birth, as an earlier row."""
    ALREADY_ON_ROSTER = "already_on_roster"
    """The roster has this NPI, or this name (and date of birth, when both have one)."""
    INVALID_ROW = "invalid_row"
    """The row breaks another rule of a provider."""
    DATE_OF_BIRTH_UNREADABLE = "date_of_birth_unreadable"
    """Not a date in a known format, or not a past date: imported without it."""
    LICENSE_STATE_INVALID = "license_state_invalid"
    """The licence state is not two letters: imported without the licence."""
    LICENSE_INCOMPLETE = "license_incomplete"
    """A licence number without a state, or a state without a number: imported without it."""
    ROLE_UNRECOGNIZED = "role_unrecognized"
    """A role nobody knows: imported with the role a blank cell gets."""


SEVERITY: Final[Mapping[ImportProblemCode, ImportSeverity]] = {
    ImportProblemCode.NAME_MISSING: ImportSeverity.ERROR,
    ImportProblemCode.NPI_INVALID: ImportSeverity.ERROR,
    ImportProblemCode.DUPLICATE_IN_FILE: ImportSeverity.ERROR,
    ImportProblemCode.ALREADY_ON_ROSTER: ImportSeverity.ERROR,
    ImportProblemCode.INVALID_ROW: ImportSeverity.ERROR,
    ImportProblemCode.DATE_OF_BIRTH_UNREADABLE: ImportSeverity.WARNING,
    ImportProblemCode.LICENSE_STATE_INVALID: ImportSeverity.WARNING,
    ImportProblemCode.LICENSE_INCOMPLETE: ImportSeverity.WARNING,
    ImportProblemCode.ROLE_UNRECOGNIZED: ImportSeverity.WARNING,
}


def _synonyms(column: ImportColumn, *headers: str) -> dict[str, ImportColumn]:
    return {header_key(header): column for header in headers}


def header_key(text: str) -> str:
    """A header as compared: case folded, spaces, underscores and hyphens removed."""
    return re.sub(r"[\s_\-]+", "", text.strip().casefold())


HEADER_SYNONYMS: Final[Mapping[str, ImportColumn]] = {
    **_synonyms(ImportColumn.LAST, "last", "last name", "surname", "family name", "lname"),
    **_synonyms(ImportColumn.FIRST, "first", "first name", "given name", "fname"),
    **_synonyms(ImportColumn.MIDDLE, "middle", "middle name", "middle initial", "mi"),
    **_synonyms(ImportColumn.SUFFIX, "suffix"),
    **_synonyms(ImportColumn.NAME, "name", "full name", "provider", "provider name"),
    **_synonyms(ImportColumn.ORGANIZATION, "organization", "organization name", "org"),
    **_synonyms(ImportColumn.ORGANIZATION, "company", "practice", "entity", "business"),
    **_synonyms(ImportColumn.NPI, "npi", "npi number", "national provider identifier"),
    **_synonyms(ImportColumn.DATE_OF_BIRTH, "dob", "date of birth", "birth date", "birthdate"),
    **_synonyms(ImportColumn.LICENSE_NUMBER, "license", "licence", "license number", "lic"),
    **_synonyms(ImportColumn.LICENSE_NUMBER, "licence number", "license #"),
    **_synonyms(ImportColumn.LICENSE_STATE, "license state", "licence state", "lic state"),
    **_synonyms(ImportColumn.LICENSE_STATE, "state"),
    **_synonyms(ImportColumn.ROLE, "role", "type", "category"),
    # A column's own name, so a preview rebuilt from ``columns`` and ``values`` reads the same.
    **{header_key(column.value): column for column in ImportColumn},
}
"""Recognized headers (as :func:`header_key` reduces them) and the column each one is."""


_PRACTITIONER: Final = ("practitioner", "provider", "clinician", "doctor", "md", "np", "pa")
_WORKFORCE: Final = ("workforce", "employee", "staff", "worker", "volunteer", "manager")
_VENDOR: Final = ("vendor", "contractor", "supplier", "agency")

ROLE_SYNONYMS: Final[Mapping[str, RosterRole]] = {
    **dict.fromkeys(_PRACTITIONER, RosterRole.PRACTITIONER),
    **dict.fromkeys(_WORKFORCE, RosterRole.WORKFORCE),
    **dict.fromkeys(_VENDOR, RosterRole.VENDOR),
}
"""Role cells (lower case, letters and digits only) and the roster role each one is."""

ROLE_HINT: Final = "practitioner, workforce or vendor"
"""The accepted roles, named on an unrecognized role (never the typed value)."""

_GENERATIONAL: Final = frozenset({"jr", "sr", "ii", "iii", "iv"})
_ISO_DATE: Final = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_US_DATE: Final = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")
_NPI_NOISE: Final = re.compile(r"[\s\-]+")
_STATE: Final = re.compile(r"^[A-Za-z]{2}$")
_HEADERLESS: Final = (ImportColumn.LAST, ImportColumn.FIRST, ImportColumn.NPI)


class ImportProblem(BaseModel):
    """One problem of a row; ``detail`` says which part, never a value."""

    model_config = ConfigDict(frozen=True)

    code: ImportProblemCode
    severity: ImportSeverity
    detail: str | None = None


class ImportRow(BaseModel):
    """One data row as read: the provider it becomes (None when an error keeps it out), what
    the preview shows, and its problems."""

    model_config = ConfigDict(frozen=True)

    line: int
    """1-based data row number, the header excluded and blank rows counted, so it matches
    the spreadsheet's row less one."""
    provider: ProviderInput | None
    display: str
    """"Last, First" or the organization name, for the preview."""
    entity_kind: EntityKind
    npi: str | None
    role: RosterRole | None
    has_date_of_birth: bool = False
    name_split: bool = False
    """The name was one cell, split into its parts."""
    problems: tuple[ImportProblem, ...] = ()
    duplicate_of_line: int | None = None
    existing_provider_id: UUID | None = None
    values: Mapping[ImportColumn, str] = {}
    """The trimmed text of each recognized column of this row (the report's ``columns``, in
    file order; empty for a cell left blank or missing), so the preview can be edited and
    previewed again. The date of birth is included: it is the caller's own unsaved text,
    handed back for editing only, never stored from the preview nor logged (owner, 2026-11-12)."""


class ImportReport(BaseModel):
    """What an import read (and, on a commit, stored and checked)."""

    model_config = ConfigDict(frozen=True)

    rows: tuple[ImportRow, ...] = ()
    columns: tuple[ImportColumn, ...] = ()
    """The recognized columns, in file order."""
    ignored_columns: tuple[str, ...] = ()
    """Headers that were not recognized, or that repeat a column already recognized."""
    no_header: bool = False
    """No header was recognized and the rows did not read as a headerless list: no rows."""
    importable: int = 0
    with_warnings: int = 0
    """Importable rows that have warnings (counted in ``importable`` too)."""
    rejected: int = 0
    imported: tuple[UUID, ...] = ()
    """The providers stored, in file order; empty on a dry run."""
    recheck: MonitoringRun | None = None
    """The first check of the imported providers; None on a dry run, when nothing was
    imported, or when the check could not run at all (the import stands)."""


@dataclass(slots=True)
class _Layout:
    """How the rows are read: each cell position's column (None: ignored)."""

    columns: tuple[ImportColumn | None, ...]
    has_header: bool
    ignored: tuple[str, ...] = ()

    @property
    def recognized(self) -> tuple[ImportColumn, ...]:
        return tuple(column for column in self.columns if column is not None)


@dataclass(slots=True)
class _Draft:
    """A row being read."""

    line: int
    display: str = ""
    entity_kind: EntityKind = EntityKind.INDIVIDUAL
    npi: str | None = None
    role: RosterRole | None = None
    name_split: bool = False
    problems: list[ImportProblem] = field(default_factory=list)
    data: ProviderInput | None = None
    provider: Provider | None = None
    duplicate_of_line: int | None = None
    existing_provider_id: UUID | None = None
    cells: dict[ImportColumn, str] = field(default_factory=dict)

    def problem(self, code: ImportProblemCode, detail: str | None = None) -> None:
        self.problems.append(ImportProblem(code=code, severity=SEVERITY[code], detail=detail))

    @property
    def rejected(self) -> bool:
        return any(problem.severity is ImportSeverity.ERROR for problem in self.problems)

    def row(self) -> ImportRow:
        data = None if self.rejected else self.data
        return ImportRow(
            line=self.line,
            provider=data,
            display=self.display,
            entity_kind=self.entity_kind,
            npi=self.npi,
            role=self.role,
            has_date_of_birth=bool(self.data and self.data.date_of_birth),
            name_split=self.name_split,
            problems=tuple(self.problems),
            duplicate_of_line=self.duplicate_of_line,
            existing_provider_id=self.existing_provider_id,
            values=self.cells,
        )


class ImportProviders:
    """Reads a roster export, previews it, and on a commit stores it and checks it."""

    def __init__(self, providers: ProviderRepository, recheck: FirstChecking, clock: Clock) -> None:
        self._providers = providers
        self._recheck = recheck
        self._clock = clock

    async def run(self, organization_id: UUID, text: str, *, dry_run: bool) -> ImportReport:
        """Read ``text`` into rows for ``organization_id``'s roster; unless ``dry_run``,
        store the importable rows (all at once) and run their first check.

        Raises:
            InvalidProviderInputError: The text is over :data:`MAX_BYTES` or
                :data:`MAX_ROWS`, or cannot be read as delimited text.
            DuplicateNpiError: Another commit stored one of the importable NPIs after the
                roster was read; nothing is stored.
        """
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise InvalidProviderInputError(
                f"the import is larger than {MAX_BYTES // (1024 * 1024)} MB"
            )
        # A byte-order mark starts many spreadsheet exports; NUL is never text (nor storable).
        records = _read(text.removeprefix("\ufeff").replace("\x00", ""))
        if not records:
            return ImportReport()
        layout = _layout(records[0][1], records)
        if layout is None:
            return ImportReport(no_header=True)
        body = records[1:] if layout.has_header else records
        if len(body) > MAX_ROWS:
            raise InvalidProviderInputError(f"the import has more than {MAX_ROWS:,} rows")
        now = self._clock.now()
        # Data rows count from 1 after the header (from the first record without one).
        start = records[0][0] if layout.has_header else records[0][0] - 1
        drafts = [_parse(number - start, cells, layout.columns, now) for number, cells in body]
        _mark_duplicates_in_file(drafts)
        if any(not draft.rejected for draft in drafts):
            roster = [provider for provider, _ in await self._providers.list_all(organization_id)]
            _mark_already_on_roster(drafts, roster)
        _build(drafts, organization_id, now)
        rows = tuple(draft.row() for draft in drafts)
        report = ImportReport(
            rows=rows,
            columns=layout.recognized,
            ignored_columns=layout.ignored,
            importable=sum(1 for row in rows if row.provider is not None),
            with_warnings=sum(1 for row in rows if row.provider is not None and row.problems),
            rejected=sum(1 for row in rows if row.provider is None),
        )
        new = [draft.provider for draft in drafts if draft.provider and not draft.rejected]
        if dry_run or not new:
            return report
        await self._providers.add_many(organization_id, new)
        ids = tuple(provider.id for provider in new)
        recheck: MonitoringRun | None = None
        try:
            recheck = await self._recheck.run_for(organization_id, ids, trigger=IMPORT_TRIGGER)
        except Exception as error:
            logger.warning(
                "first check after import failed organization_id=%s providers=%d error=%s",
                organization_id,
                len(ids),
                type(error).__name__,
            )
        logger.info("import stored organization_id=%s providers=%d", organization_id, len(ids))
        return report.model_copy(update={"imported": ids, "recheck": recheck})


def _read(text: str) -> list[tuple[int, list[str]]]:
    """The non-blank records of ``text``, each with its 0-based record number and its cells
    stripped (trailing empty cells dropped)."""
    if not text.strip():
        return []
    dialect = _dialect(text)
    records: list[tuple[int, list[str]]] = []
    try:
        for number, raw in enumerate(csv.reader(io.StringIO(text, newline=""), dialect)):
            cells = [cell.strip() for cell in raw]
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                records.append((number, cells))
    except csv.Error as error:
        raise InvalidProviderInputError(
            f"the import could not be read as delimited text (record {len(records) + 1})"
        ) from error
    return records


def _dialect(text: str) -> type[csv.Dialect]:
    """Tab-separated when the first line has a tab (a spreadsheet paste); otherwise what
    :class:`csv.Sniffer` finds among comma, tab, semicolon and bar; a comma when it finds
    nothing."""
    lines = [line for line in text.splitlines() if line.strip()][:_SNIFF_LINES]
    if lines and "\t" in lines[0]:
        return csv.excel_tab
    try:
        return csv.Sniffer().sniff("\n".join(lines), delimiters=_DELIMITERS)
    except csv.Error:
        return csv.excel


def _layout(header: Sequence[str], records: Sequence[tuple[int, list[str]]]) -> _Layout | None:
    """The columns of the first record when it is a header; the headerless order
    (:data:`_HEADERLESS`) when it reads as data and every record has 2 or 3 cells; None when
    neither."""
    columns: list[ImportColumn | None] = []
    for cell in header:
        column = HEADER_SYNONYMS.get(header_key(cell))
        columns.append(None if column in columns else column)
    if any(column is not None for column in columns):
        if ImportColumn.LICENSE_STATE in columns and ImportColumn.LICENSE_NUMBER not in columns:
            # A state without a licence number column is an address's, not a licence's.
            columns[columns.index(ImportColumn.LICENSE_STATE)] = None
        ignored = tuple(
            cell for cell, column in zip(header, columns, strict=True) if column is None and cell
        )
        return _Layout(tuple(columns), has_header=True, ignored=ignored)
    widths = {len(cells) for _, cells in records}
    if widths <= {2, 3} and any(_looks_like_data(cell) for cell in header):
        return _Layout(_HEADERLESS[: max(widths)], has_header=False)
    return None


def _looks_like_data(cell: str) -> bool:
    """A cell no header would have: ten digits (an NPI) or a date."""
    cleaned = _NPI_NOISE.sub("", cell)
    return (len(cleaned) == 10 and cleaned.isdigit()) or _date(cell) is not None


def _parse(
    line: int, cells: Sequence[str], columns: Sequence[ImportColumn | None], now: datetime
) -> _Draft:
    """One record read into a draft: its values and the problems they have."""
    padded = [*cells, *[""] * (len(columns) - len(cells))]
    draft = _Draft(line=line)
    draft.cells = {
        column: cell for column, cell in zip(columns, padded, strict=False) if column is not None
    }
    values = {column: cell for column, cell in draft.cells.items() if cell}
    last, first = values.get(ImportColumn.LAST), values.get(ImportColumn.FIRST)
    middle, suffix = values.get(ImportColumn.MIDDLE), values.get(ImportColumn.SUFFIX)
    whole = values.get(ImportColumn.NAME)
    if last is None and first is None and whole is not None:
        last, first, middle, split_suffix = split_name(whole)
        suffix = suffix or split_suffix
        draft.name_split = True
    organization = values.get(ImportColumn.ORGANIZATION)
    npi = _npi(values.get(ImportColumn.NPI), draft)
    born = _date_of_birth(values.get(ImportColumn.DATE_OF_BIRTH), now, draft)
    licenses = _license(
        values.get(ImportColumn.LICENSE_NUMBER), values.get(ImportColumn.LICENSE_STATE), draft
    )
    if last is None and organization is not None:
        draft.entity_kind = EntityKind.ORGANIZATION
        draft.display = organization
        name = None
    else:
        draft.display = ", ".join(part for part in (last, first) if part)
        missing = [label for label, part in (("last", last), ("first", first)) if not part]
        if missing:
            draft.problem(ImportProblemCode.NAME_MISSING, " and ".join(missing) + " name")
        name = (
            PersonName(last=last, first=first, middle=middle, suffix=suffix)
            if last and first
            else None
        )
        organization = None
    draft.role = _role(values.get(ImportColumn.ROLE), npi, draft)
    if not draft.rejected:
        draft.data = ProviderInput(
            entity_kind=draft.entity_kind,
            person_name=name,
            organization_name=organization,
            npi=npi,
            date_of_birth=born,
            licenses=licenses,
            role=draft.role,
        )
    return draft


def split_name(whole: str) -> tuple[str | None, str | None, str | None, str | None]:
    """A name in one cell as (last, first, middle, suffix): "Last, First Middle" on the
    comma, otherwise "First [Middle] Last" on spaces (the last word is the last name). A
    trailing generational suffix (Jr, III) is kept as the suffix and a trailing credential
    (MD, RN) is dropped."""
    if "," in whole:
        head, _, rest = whole.partition(",")
        words, suffix = _trailing_suffix(rest.split())
        last = head.strip() or None
        first = words[0] if words else None
        return last, first, " ".join(words[1:]) or None, suffix
    words, suffix = _trailing_suffix(whole.split())
    if len(words) == 1:
        return words[0], None, None, suffix
    if not words:
        return None, None, None, suffix
    return words[-1], words[0], " ".join(words[1:-1]) or None, suffix


def _trailing_suffix(words: list[str]) -> tuple[list[str], str | None]:
    """``words`` without trailing credentials and generational suffixes (never the only word
    left), and the generational suffix found, if any."""
    suffix = None
    while len(words) > 1:
        key = re.sub(r"[^0-9a-z]", "", words[-1].casefold())
        if key not in CREDENTIAL_SUFFIXES and key not in _GENERATIONAL:
            break
        word = words.pop()
        if key in _GENERATIONAL and suffix is None:
            suffix = word.rstrip(".,")
    return words, suffix


def _npi(cell: str | None, draft: _Draft) -> str | None:
    """The NPI with spaces and dashes removed; ``npi_invalid`` when it is not one. A cell
    without a digit ("N/A", "none") is a blank one."""
    if cell is None or not any(char.isdigit() for char in cell):
        return None
    cleaned = _NPI_NOISE.sub("", cell)
    draft.npi = cleaned
    if not validate_npi(cleaned):
        draft.problem(ImportProblemCode.NPI_INVALID)
        return None
    return cleaned


def _date(cell: str) -> date | None:
    """``YYYY-MM-DD``, ``MM/DD/YYYY``, ``M/D/YYYY`` or ``MM-DD-YYYY`` as a date; None when it
    is none of them or no such day exists."""
    if found := _ISO_DATE.match(cell):
        year, month, day = (int(part) for part in found.groups())
    elif found := _US_DATE.match(cell):
        month, day, year = (int(part) for part in found.groups())
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_of_birth(cell: str | None, now: datetime, draft: _Draft) -> date | None:
    """The date of birth; ``date_of_birth_unreadable`` when it is not a past date."""
    if cell is None:
        return None
    born = _date(cell)
    if born is None or born >= now.date():
        draft.problem(ImportProblemCode.DATE_OF_BIRTH_UNREADABLE)
        return None
    return born


def _license(number: str | None, state: str | None, draft: _Draft) -> tuple[License, ...]:
    """The licence when both its number and a two-letter state are given; a warning when
    only one part is, or the state is not two letters."""
    if number is None and state is None:
        return ()
    if state is not None and not _STATE.match(state):
        draft.problem(ImportProblemCode.LICENSE_STATE_INVALID)
        return ()
    if number is None or state is None:
        missing = "state" if state is None else "number"
        draft.problem(ImportProblemCode.LICENSE_INCOMPLETE, f"no licence {missing}")
        return ()
    return (License(number=number, state=state.upper()),)


def _role(cell: str | None, npi: str | None, draft: _Draft) -> RosterRole:
    """The role the cell names; blank (or unknown, with a warning): a practitioner with an
    NPI, a vendor for an organization without one, otherwise workforce."""
    if cell is not None:
        role = ROLE_SYNONYMS.get(re.sub(r"[^0-9a-z]", "", cell.casefold()))
        if role is not None:
            return role
        draft.problem(ImportProblemCode.ROLE_UNRECOGNIZED, ROLE_HINT)
    if npi is not None:
        return RosterRole.PRACTITIONER
    if draft.entity_kind is EntityKind.ORGANIZATION:
        return RosterRole.VENDOR
    return RosterRole.WORKFORCE


def _identity(name: PersonName | None, organization: str | None) -> tuple[str, ...] | None:
    """What a party without an NPI is compared by: its kind and name keys; None when it has
    no name to compare."""
    if name is not None:
        return ("individual", normalize_name(name.last), normalize_name(name.first))
    if organization:
        return ("organization", normalize_organization_name(organization))
    return None


def _importable(drafts: Sequence[_Draft]) -> Iterator[tuple[_Draft, ProviderInput]]:
    for draft in drafts:
        if draft.data is not None and not draft.rejected:
            yield draft, draft.data


def _mark_duplicates_in_file(drafts: Sequence[_Draft]) -> None:
    """``duplicate_in_file`` on every importable row repeating an earlier importable one: by
    NPI, else (rows without an NPI) by name and date of birth."""
    by_npi: dict[str, int] = {}
    by_name: dict[tuple[object, ...], int] = {}
    for draft, data in _importable(drafts):
        if data.npi is not None:
            earlier = by_npi.setdefault(data.npi, draft.line)
        else:
            identity = _identity(data.person_name, data.organization_name)
            if identity is None:
                continue
            earlier = by_name.setdefault((*identity, data.date_of_birth), draft.line)
        if earlier != draft.line:
            draft.duplicate_of_line = earlier
            draft.problem(ImportProblemCode.DUPLICATE_IN_FILE, f"line {earlier}")


def _mark_already_on_roster(drafts: Sequence[_Draft], roster: Sequence[Provider]) -> None:
    """``already_on_roster`` on every importable row the roster has: by NPI, else by name
    (and date of birth when both have one)."""
    by_npi: dict[str, UUID] = {}
    by_name: dict[tuple[str, ...], list[Provider]] = {}
    for provider in roster:
        if provider.npi is not None:
            by_npi.setdefault(provider.npi, provider.id)
        identity = _identity(provider.person_name, provider.organization_name)
        if identity is not None:
            by_name.setdefault(identity, []).append(provider)
    for draft, data in _importable(drafts):
        existing: UUID | None = None
        if data.npi is not None:
            existing = by_npi.get(data.npi)
        elif (identity := _identity(data.person_name, data.organization_name)) is not None:
            existing = next(
                (
                    provider.id
                    for provider in by_name.get(identity, ())
                    if data.date_of_birth is None
                    or provider.date_of_birth is None
                    or provider.date_of_birth == data.date_of_birth
                ),
                None,
            )
        if existing is not None:
            draft.existing_provider_id = existing
            draft.problem(ImportProblemCode.ALREADY_ON_ROSTER)


def _build(drafts: Sequence[_Draft], organization_id: UUID, now: datetime) -> None:
    """The provider of every importable row, through :meth:`RegisterProvider.build`; created
    a microsecond apart in file order, so the roster keeps the file's order."""
    for index, (draft, data) in enumerate(_importable(drafts)):
        try:
            draft.provider = RegisterProvider.build(
                organization_id, data, now + timedelta(microseconds=index)
            )
        except InvalidProviderInputError as error:
            draft.problem(ImportProblemCode.INVALID_ROW, str(error))
