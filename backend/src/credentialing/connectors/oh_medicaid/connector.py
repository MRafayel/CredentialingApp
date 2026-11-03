"""Connector for the Ohio Department of Medicaid provider exclusion and suspension list.

Source facts (survey of 2026-10-16 to 2026-10-22, re-checked 2026-11-02):

- Landing page: https://medicaid.ohio.gov/resources-for-providers/enrollment-and-support/
  provider-enrollment/provider-exclusion-and-suspension-list ("Provider Exclusion &
  Suspension List", about 381 KB, nginx behind CloudFront, ``max-age=86400``, no validators;
  the origin takes about 15 s to answer). It links the workbook as "Ohio Medicaid Provider
  Exclusion Suspension List (Updated 10/15/2026)" on the state's Cloudinary asset host:
  ``https://dam.assets.ohio.gov/raw/upload/medicaid.ohio.gov/Providers/Enrollment%20and%20
  Support/Enrollment/ExclusionSuspensionList_10_15_2026-3.xlsx``. The date (and an upload
  counter) is in the file name, so change signal ``LINK_DISCOVERY``: one landing GET per
  poll, the download only on a new link. Polled weekly with a 10 s pause before every request
  (our choice: the site declares none, and speed matters less than politeness).
- The page asks readers to use the file on the page "instead of storing one on your computer
  system": nothing is kept but our normalized records and the snapshot's hash, and the weekly
  poll re-reads the page.
- From 2026-10-16 to 2026-10-22 CloudFront answered scripts with a static 404 page on every
  path, a block disguised as a missing page; on 2026-11-02 the landing page answered ``200``
  to a plain GET and to headed Chrome alike. ``browser_fallback`` repeats a refused (or 404)
  request in headed Chrome, which passes; headless Chrome was blocked in 2026-10.
- The file: xlsx, 237,206 bytes (2026-10-15 edition), ``Server: Cloudinary``, ETag and
  Last-Modified, ``max-age=31536000``; plain HTTP downloads it.
- Sheets: "Instructions" (empty cells), "Individuals" (header on row 1: ``Last Name, First
  Name, Middle Name, DOB, NPI, Provider ID, Status, Action Date, Date Added, Provider Type``;
  1,867 rows and about 200 formatted empty rows) and "Organizations" (header on row 1:
  ``NPI, Organization Name, Address 1, Address 2, City, State, Zip Code, Provider ID, Status,
  Action Date, Date Added, Provider Type``; 255 rows). Rows of "Individuals" are people, rows
  of "Organizations" organizations: the sheet decides, never the name.
- ``Status`` is the action: "Excluded" (exclusion), "Suspended" (also "suspended" and the
  typo "Susepnded": suspension), "Terminated" (termination), kept as ``action_raw``; an empty
  status (3 rows) is ``other``. ``Action Date`` is the effective date (an Excel date, a few
  typed M/D/YYYY). There is no end or reinstatement date and no reason: a provider leaves by
  being removed from the list. ``Date Added`` (when the row was added; dates, a few notes)
  and ``Provider Type`` ("21 - Professional Medical Group", several joined with ", ") stay
  in ``raw``.
- ``DOB`` (individuals, an Excel date on nearly every row, a few typed M/D/YYYY): stored as
  the record's date of birth for matching, never logged and never shown by default.
- ``NPI``: a number, text, empty, a space, or several separated by commas ("..., 123456" with
  a truncated last one): every valid NPI is read (the first the record's NPI, the others
  related NPIs); invalid ones stay in ``raw``.
- ``Provider ID``: Ohio Medicaid provider numbers, seven digits, often with a leading zero,
  several separated by commas or "/". Numeric cells lost their leading zeros (5 or 6 digits):
  the first number is the state provider ID, restored to seven digits; a first token that is
  not a number of 5 to 7 digits ("REG ID ...", eight digits) gives none.
- Individuals' names: ``Last Name`` may carry a former name in parentheses ("SAMPLE
  (MAIDEN)", also two of them) or after "aka", each the alias "MAIDEN, First Middle"; a
  generational suffix ("JR", ", Jr.", "III") is split off. ``First Name`` may carry a
  nickname in parentheses or quotes ("Avery “Ave”"): the alias "LAST, Ave". A trailing
  initial in ``First Name`` with an empty ``Middle Name`` ("Avery Q.") is the middle name.
- Organizations: the name may carry its trade names after "dba" or in parentheses: aliases
  (``names.py`` rules). A professional corporation's name ("SAMPLE AVERY DO LLC dba ...")
  stays the organization, cut at its alias markers. The address comes from the five
  columns; two ZIP cells are formatted as dates, so they are read back as the number Excel
  stored (the day count since 1899-12-30).
- Rows repeating one identity are merged, joining ``NPI``, ``Provider ID``, ``Date Added`` and
  ``Provider Type``: on 2026-11-02 one organization listed twice under two provider types.
- Live run 2026-11-02 (2026-10-15 edition, read end to end over plain HTTP; the browser was
  not needed): 2,122 rows, 2,121 records, none skipped, 1 merged, no unreadable values.
  1,867 individuals and 254 organizations; exclusion 1,875, suspension 241, termination 2,
  other 3; effective date on 2,112; date of birth on 1,863; NPI on 794 (related NPIs on 32);
  state provider ID on 1,403 (14 with restored leading zeros); address on all 254
  organizations (3 outside Ohio); aliases on 52 records (30 former names, 5 nicknames, 17
  organization trade names); 29 suffixes; 5 trailing initials moved to the middle name.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    ParsedName,
    holds_only_credentials,
    parse_name_field,
    split_aliases,
    split_suffix,
)
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    collapse,
    parse_address,
    parse_npi_list,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    PersonName,
    SourceSnapshot,
)

INDIVIDUALS: Final = "Individuals"
ORGANIZATIONS: Final = "Organizations"
LAST_NAME: Final = "Last Name"
FIRST_NAME: Final = "First Name"
MIDDLE_NAME: Final = "Middle Name"
DOB: Final = "DOB"
NPI: Final = "NPI"
PROVIDER_ID: Final = "Provider ID"
STATUS: Final = "Status"
ACTION_DATE: Final = "Action Date"
DATE_ADDED: Final = "Date Added"
PROVIDER_TYPE: Final = "Provider Type"
ORGANIZATION_NAME: Final = "Organization Name"
ZIP_CODE: Final = "Zip Code"

INDIVIDUAL_COLUMNS: Final = (
    LAST_NAME,
    FIRST_NAME,
    MIDDLE_NAME,
    DOB,
    NPI,
    PROVIDER_ID,
    STATUS,
    ACTION_DATE,
    DATE_ADDED,
    PROVIDER_TYPE,
)
ORGANIZATION_COLUMNS: Final = (
    NPI,
    ORGANIZATION_NAME,
    "Address 1",
    "Address 2",
    "City",
    "State",
    ZIP_CODE,
    PROVIDER_ID,
    STATUS,
    ACTION_DATE,
    DATE_ADDED,
    PROVIDER_TYPE,
)

STATUS_ACTIONS: Final = (
    ("exclu", ExclusionAction.EXCLUSION),
    ("susp", ExclusionAction.SUSPENSION),
    ("susep", ExclusionAction.SUSPENSION),
    ("terminat", ExclusionAction.TERMINATION),
)
"""``Status`` words, first match wins; "Susepnded" is a spelling the list uses once."""

JOINED: Final = (NPI, PROVIDER_ID, DATE_ADDED, PROVIDER_TYPE)
"""Raw columns that keep every distinct value when rows of one identity are merged."""

_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "OH"
_FILE_LINK: Final = re.compile(r"/[^/?#]*Exclusion[^/?#]*Suspension[^/?#]*\.xlsx$", re.IGNORECASE)
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")
_NICKNAME: Final = re.compile(r"\(([^()]*)\)|[“\"]([^“”\"]*)[”\"]|(?<!\S)'([^']+)'(?!\S)")
_INITIAL: Final = re.compile(r"^[A-Za-z]\.?$")
_ID_SEPARATORS: Final = re.compile(r"[\s,;/]+")
_PROVIDER_NUMBER: Final = re.compile(r"^[0-9]{5,7}$")
_ISO_DATE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EXCEL_EPOCH: Final = date(1899, 12, 30)
_PROVIDER_NUMBER_DIGITS: Final = 7

LANDING_URL: Final = (
    "https://medicaid.ohio.gov/resources-for-providers/enrollment-and-support/"
    "provider-enrollment/provider-exclusion-and-suspension-list"
)
"""The ODM page that links the current workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="oh_medicaid",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Ohio Medicaid Provider Exclusion and Suspension List",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
)


class OhMedicaidConnector(BulkListConnectorBase):
    """Finds the current workbook on the ODM page, downloads it and maps both sheets."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "Ohio Medicaid Provider Exclusion Suspension List" workbook link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map the people of "Individuals" and the organizations of "Organizations";
        rows repeating an identity are merged."""
        people = build_records(
            read_sheet(body, sheet=INDIVIDUALS, expected_columns=INDIVIDUAL_COLUMNS),
            partial(_person_record, snapshot),
        )
        organizations = build_records(
            read_sheet(body, sheet=ORGANIZATIONS, expected_columns=ORGANIZATION_COLUMNS),
            partial(_organization_record, snapshot),
        )
        records = (*people.records, *organizations.records)
        merged = merge_duplicates(records, joined=JOINED)
        unreadable: Counter[str] = Counter()
        for sheet, parsed in ((INDIVIDUALS, people), (ORGANIZATIONS, organizations)):
            for column, count in parsed.unreadable.items():
                unreadable[f"{sheet} {column}"] += count
        return ParsedList(
            records=merged,
            rows=people.rows + organizations.rows,
            skipped=people.skipped + organizations.skipped,
            unreadable=dict(unreadable),
            merged=len(records) - len(merged),
        )


def parse_person(row: Mapping[str, str]) -> ParsedName:
    """The person of an "Individuals" row, with the other names the cells give (module rules)."""
    last_cell = collapse(row[LAST_NAME])
    if last_cell is None:
        raise ParseError("row has no last name")
    former = [*_names_in(_PARENTHESIZED.findall(last_cell))]
    primary, marked = split_aliases(_PARENTHESIZED.sub(" ", last_cell))
    last, suffix = split_suffix(primary)
    if not last:
        raise ParseError("row has no last name")
    former += marked
    first_cell = collapse(row[FIRST_NAME]) or ""
    nicknames = [*_names_in("".join(found) for found in _NICKNAME.findall(first_cell))]
    first, other_given = split_aliases(_NICKNAME.sub(" ", first_cell))
    nicknames += other_given
    middle = collapse(row[MIDDLE_NAME])
    words = first.split()
    if middle is None and len(words) > 1 and _INITIAL.match(words[-1]):
        first, middle = " ".join(words[:-1]), words[-1]
    person = PersonName(last=last, first=first, middle=middle, suffix=suffix)
    given = " ".join(part for part in (first, middle) if part)
    aliases = [f"{name}, {given}" if given else name for name in former]
    aliases += [f"{last}, {name}" if len(name.split()) == 1 else name for name in nicknames]
    return ParsedName(person=person, organization=None, aliases=tuple(dict.fromkeys(aliases)))


def parse_organization(name_cell: str) -> ParsedName:
    """The organization of an "Organizations" row; its dba and parenthesized names are aliases."""
    name = collapse(name_cell)
    if name is None:
        raise ParseError("row has no organization name")
    listed = parse_name_field(name, organization_hint=True)
    if listed.organization is not None:
        return ParsedName(person=None, organization=listed.organization, aliases=listed.aliases)
    # A professional corporation ("SAMPLE AVERY DO LLC dba ...") reads as its person; on this
    # sheet it is the organization, named as written before its first alias marker.
    primary, marked = split_aliases(name)
    names = (primary, *marked) if primary else marked
    return ParsedName(person=None, organization=names[0] if names else name, aliases=names[1:])


def parse_provider_id(cell: str) -> str | None:
    """The first Ohio Medicaid provider number of the cell, with its lost leading zeros."""
    tokens = [token for token in _ID_SEPARATORS.split(cell) if token]
    if not tokens or not _PROVIDER_NUMBER.match(tokens[0]):
        return None
    return tokens[0].zfill(_PROVIDER_NUMBER_DIGITS)


def parse_zip(cell: str) -> str:
    """A ZIP cell as text; a ZIP formatted as a date is read back as the number Excel stored."""
    value = cell.strip()
    if _ISO_DATE.match(value):
        try:
            return str((date.fromisoformat(value) - _EXCEL_EPOCH).days)
        except ValueError:
            return value
    return value


def _names_in(texts: Iterable[str]) -> list[str]:
    """The names inside parentheses or quotes: split at alias markers, credentials left out."""
    names: list[str] = []
    for text in texts:
        primary, marked = split_aliases(text)
        names += [name for name in (primary, *marked) if name and not holds_only_credentials(name)]
    return names


def _status(row: Mapping[str, str]) -> tuple[ExclusionAction, str | None]:
    status = clean(row[STATUS])
    if status is None:
        return ExclusionAction.OTHER, None
    return classify_action(status, STATUS_ACTIONS, default=ExclusionAction.OTHER), status


def _person_record(
    snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues
) -> ExclusionRecord:
    party = parse_person(row)
    npis = parse_npi_list(row[NPI])
    action, action_raw = _status(row)
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        aliases=party.aliases,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=parse_provider_id(row[PROVIDER_ID]),
        date_of_birth=issues.date(DOB, row[DOB], _DATE_FORMATS),
        action=action,
        action_raw=action_raw,
        effective_date=issues.date(ACTION_DATE, row[ACTION_DATE], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )


def _organization_record(
    snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues
) -> ExclusionRecord:
    party = parse_organization(row[ORGANIZATION_NAME])
    npis = parse_npi_list(row[NPI])
    action, action_raw = _status(row)
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        organization_name=party.organization,
        aliases=party.aliases,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=parse_provider_id(row[PROVIDER_ID]),
        address=parse_address(
            line1=row["Address 1"],
            line2=row["Address 2"],
            city=row["City"],
            state=row["State"],
            postal_code=parse_zip(row[ZIP_CODE]),
        ),
        action=action,
        action_raw=action_raw,
        effective_date=issues.date(ACTION_DATE, row[ACTION_DATE], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
