"""Connector for the Delaware DHSS/DMAP "Delaware Listing of Sanctioned Providers".

Source facts (observed 2026-10-31):

- File: ``https://medicaidpublications.dhss.delaware.gov/docs/Search?Command=Core_Download&
  EntryId=1671``, a stable link that answers ``302`` to ``.../docs/DesktopModules/Bring2mind/
  DMX/API/Entries/Download?Command=Core_Download&EntryId=1671&language=en-US&PortalId=0&
  TabId=94`` on the same host. A PDF of 926,329 bytes ("Acrobat PDFMaker 26 for Excel"),
  Last-Modified 2026-10-15, ``Cache-Control: no-cache``. No robots.txt on the host.
- The ETag is ``"1671"``, the EntryId, and never changes with the content, so a conditional
  request or a header comparison would report a new file as unchanged. The change signal is
  ``CONTENT_HASH``: a plain GET every week, compared by hash.
- 37 landscape pages (1616 x 1249 pt), one ruled table each, the header repeated on every
  page; the "Page n of 37" footer and the report date are outside the table. Columns:
  ``Year | Sanctioned Provider Name | NPI # | Taxonomy # | License # | DEA # | OIG / Medicaid
  Sanction | Effective Date | Reinstated Date | Comments``; 1,824 data rows, wrapped cells
  (mostly Comments) keep their line breaks.
- Names are written first name first, credentials after a comma or a space ("First M.
  Last, MD", "First Last MD, DMD", "First Last, PCA"); organizations have a business word
  ("Sample Health Care Inc"). Some names end in a parenthesized role ("(Employee)", "(PCA)",
  "(Owner/Exec)") or a credential, neither of which is an alias; other parentheses hold a
  maiden name, kept as an alias. A few cells are "Last, First" (:func:`parse_party`).
- ``NPI #``: an NPI, "N/A", or now and then a licence number; several NPIs are split on line
  breaks, the first is the record's. ``Taxonomy #`` is a taxonomy code, kept raw only.
- ``License #``: the first licence of the cell, without a "Lic#" label or an "-Expired"
  status. Its state is the one in parentheses ("RN123456 (PA)") or else the sanctioning
  authority's (:data:`LICENCE_STATES`), else Delaware's: Pennsylvania's rows without a state
  carry Pennsylvania-format numbers (290, against 2 in Delaware format and 41 bare digits or
  other), Delaware boards' rows Delaware-format numbers ("C1-0001234").
  "PROMISe#" values are Pennsylvania Medicaid provider numbers, not licences: raw only.
- ``DEA #`` holds DEA registration numbers. They are never stored, not even in ``raw``
  (:data:`NOT_STORED`): they enable prescription fraud and matching never needs them.
- ``OIG / Medicaid Sanction`` names the sanctioning authority, in 85 spellings
  ("Pennsylvania Department of Human Services" on about 1,360 rows, "OIG", "Medicaid
  Sanction", Delaware boards, "Delaware Secretary of State"). It is kept as ``action_raw``;
  the action is an exclusion when it names the OIG, otherwise a sanction. ``Comments`` is the
  reason and ``Year`` stays raw.
- ``Effective Date`` is M/D/YYYY, with a few unreadable values ("7/20/014", text) counted.
  ``Reinstated Date`` is blank, "Indefinite", a date (some in 2029-2030: the scheduled end of
  the sanction), a text with a date ("Reinstated effective 07/28/2015"), or a word
  ("Revoked", "Active", "Probation"). A date, alone or in a text, is the end date; anything
  else is no end date. A date earlier than the effective date is a data error: counted as
  unreadable and left out, so the row stays listed for review instead of reading as ended.
- Rows repeating one identity are merged (67 groups, 85 extra rows on 2026-10-31): 59 groups
  differ in ``Reinstated Date`` (one row ended, another ongoing or ending later), 15 also or
  only in name spelling, 4 are exact repeats. The merged record has no end date when any of
  its rows has none, otherwise the latest (``merge_duplicates``).
- First sync 2026-10-31: 1,824 rows, none skipped, 1,739 records (85 merged); 1,647
  individuals and 92 organizations; NPI on 603 (a related NPI on 7); licence on 810
  (Pennsylvania 414, Delaware 362, other states 34); aliases on 30. Actions: sanction 1,625,
  exclusion 114. End date on 471, 333 of them in the future. Unreadable: 8 effective dates,
  5 reinstated dates (4 earlier than the effective date). Names: 16 cells read as "Last,
  First", 49 with a description after the comma dropped, 2 people with only a last name.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    BUSINESS_WORDS,
    SUFFIXES,
    looks_like_business,
    parse_name_field,
)
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.values import (
    clean,
    collapse,
    open_ended,
    parse_npi_list,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)

NAME: Final = "Sanctioned Provider Name"
LICENSE: Final = "License #"
DEA: Final = "DEA #"
AUTHORITY: Final = "OIG / Medicaid Sanction"
EFFECTIVE: Final = "Effective Date"
REINSTATED: Final = "Reinstated Date"
COMMENTS: Final = "Comments"
COLUMNS: Final = (
    "Year",
    NAME,
    "NPI #",
    "Taxonomy #",
    LICENSE,
    DEA,
    AUTHORITY,
    EFFECTIVE,
    REINSTATED,
    COMMENTS,
)
"""The table's header, as printed on every page."""

JOINED: Final = (REINSTATED, COMMENTS, "Year", AUTHORITY, "NPI #", "Taxonomy #")
"""Raw columns that keep every distinct value when rows of one identity are merged."""

NOT_STORED: Final = frozenset({DEA})
"""Columns left out of ``raw``: a DEA registration number enables prescription fraud."""

LOCAL_CREDENTIALS: Final = frozenset(
    {"crnp", "aprn", "lpcmh", "plpcmh", "lmt", "lsw", "rdh", "lrt", "ldn", "efda", "pn", "hha"}
)
"""Credentials this list writes after names that the shared suffix list does not know."""

ROLE_WORDS: Final = frozenset(
    {
        "employee", "owner", "exec", "exex", "manager", "consumer", "beneficiary", "recipient",
        "entity", "therapist", "massage", "pharmacist", "ceo",
    }
)  # fmt: skip
"""Words of a role or profession written after a name: dropped from the name, kept raw."""

ORGANIZATION_WORDS: Final = frozenset(
    {"WELLNESS", "BEHAVIORAL", "RECOVERY", "APOTHECARY", "COMMUNITY", "FAMILY"}
)
"""Business words of this list that the shared business word list does not have."""

LICENCE_STATES: Final = (
    ("pennsylvania", "PA"),
    ("pa dept", "PA"),
    ("maryland", "MD"),
    ("md depart", "MD"),
    ("ohio", "OH"),
    ("michigan", "MI"),
    ("california", "CA"),
)
"""Authorities naming another state, first match wins; any other authority is Delaware's."""

_STATE: Final = "DE"
_DATE_FORMATS: Final = ("%m/%d/%Y",)
_OIG: Final = re.compile(r"\bOIG\b|\binspector general\b", re.IGNORECASE)
_EMBEDDED_DATE: Final = re.compile(r"(?<![0-9])[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}(?![0-9])")
_OPEN_ENDED: Final = frozenset({"INDEFINITE"})
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")
_GLUED_INITIAL: Final = re.compile(r"\b([A-Z])\.(?=[A-Z][a-z])")
_TOKEN_SEPARATORS: Final = re.compile(r"[\s/]+")
_CAPITALS: Final = re.compile(r"^[A-Z]{2,6}$")
_ALIAS_MARKER: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:a\.?k\.?a\.?|a/k/a|d\.?b\.?a\.?|d/b/a|f\.?k\.?a\.?|f/k/a)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_PARENTHESIZED_ALIAS: Final = re.compile(rf"\(\s*({_ALIAS_MARKER.pattern})\s*:?([^()]*)\)", re.I)
_ALIAS_WORDS: Final = frozenset({"AKA", "DBA", "FKA"})
_NUMBER_AFTER_NAME: Final = re.compile(r"\s*#[\sA-Za-z0-9-]*[0-9][\sA-Za-z0-9-]*$")
_EDGE_PUNCTUATION: Final = " ,;:-"
_NOT_AVAILABLE: Final = re.compile(r"(?<![A-Za-z])N/A(?![A-Za-z])", re.IGNORECASE)
_LICENSE_SEPARATORS: Final = re.compile(r"[;,/&\n]+|\band\b")
_LONE_STATE_LINE: Final = re.compile(r"\s*\n\s*(\(\s*[A-Za-z]{2}\s*\))")
_LICENSE_STATE: Final = re.compile(r"\(\s*([A-Za-z]{2})\s*\)")
_LICENSE_LABEL: Final = re.compile(r"^(?:DE\s+)?Lic(?:ense)?\s*#?\s*:?\s*", re.IGNORECASE)
_LICENSE_STATUS: Final = re.compile(r"\s*-\s*(?:expired|surrendered)\s*$", re.IGNORECASE)
_PROVIDER_NUMBER: Final = re.compile(r"^\s*promise\b", re.IGNORECASE)
_NO_LICENSE: Final = frozenset({"NO LICENSE"})

DOWNLOAD_URL: Final = (
    "https://medicaidpublications.dhss.delaware.gov/docs/Search?Command=Core_Download&EntryId=1671"
)
"""The stable link to the PDF; it redirects to the file on the same host."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="de_dmap",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=DOWNLOAD_URL,
    display_name="Delaware Medicaid Sanctioned Providers",
    download_url=DOWNLOAD_URL,
)


class DeDmapConnector(BulkListConnectorBase):
    """Downloads the Delaware PDF and maps each table row into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every table row; rows repeating one identity are merged into one record."""
        rows = read_pdf_table(body, expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=JOINED)
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


@dataclass(frozen=True, slots=True)
class Party:
    """A row's party: a person or an organization, with the aliases the cell gives."""

    person: PersonName | None
    organization: str | None
    aliases: tuple[str, ...]

    @property
    def entity_kind(self) -> EntityKind:
        """Individual for a person, organization for a business, unknown for an empty cell."""
        if self.person is not None:
            return EntityKind.INDIVIDUAL
        if self.organization is not None:
            return EntityKind.ORGANIZATION
        return EntityKind.UNKNOWN


def parse_party(cell: str | None) -> Party:
    """Split a name cell written first name first into a person or an organization.

    A cell with a business word is an organization. Otherwise a trailing "# number" is cut
    and the cell is split at alias markers (AKA, DBA, FKA); each part loses its credentials
    and roles, in parentheses or after it: credentials (the shared list,
    :data:`LOCAL_CREDENTIALS`, or a word in capitals such as "PCA") become the suffix and
    roles are dropped. With two or more words before the first comma, the part is "First
    Last, description" and the description is dropped; with one, it is "Last, First". The
    cell always stays in ``raw``.
    """
    text = collapse(cell)
    if text is None:
        return Party(person=None, organization=None, aliases=())
    text = _PARENTHESIZED_ALIAS.sub(r" \1 \2 ", _GLUED_INITIAL.sub(r"\1. ", text))
    text = _NUMBER_AFTER_NAME.sub("", text)
    capitals_are_names = text.upper() == text
    if _is_organization(text):
        without = _without_role_parentheses(text, [], capitals_are_names=capitals_are_names)
        parsed = parse_name_field(without, organization_hint=True)
        return Party(person=parsed.person, organization=parsed.organization, aliases=parsed.aliases)
    primary, *others = _ALIAS_MARKER.split(text)
    credentials: list[str] = []
    parsed = parse_name_field(
        _name_part(primary, credentials, capitals_are_names=capitals_are_names),
        first_name_first=True,
    )
    aliases = [
        *parsed.aliases,
        *(_name_part(other, [], capitals_are_names=capitals_are_names) for other in others),
    ]
    if parsed.person is None:
        return Party(person=None, organization=parsed.organization, aliases=_kept(aliases))
    suffix = " ".join(part for part in (parsed.person.suffix, *credentials) if part) or None
    return Party(
        person=parsed.person.model_copy(update={"suffix": suffix}),
        organization=None,
        aliases=_kept(aliases),
    )


def _licence_state(authority: str | None) -> str:
    """The state that issued a licence written without one: the sanctioning authority's."""
    folded = (authority or "").casefold()
    return next((state for name, state in LICENCE_STATES if name in folded), _STATE)


def _license(text: str | None, authority: str | None) -> License | None:
    """The cell's first licence, with the state in parentheses or the authority's state.

    Pennsylvania Medicaid provider numbers ("PROMISe# 123") are not licences.
    """
    cell = _LONE_STATE_LINE.sub(r" \1", _NOT_AVAILABLE.sub(" ", text or ""))
    first = next((part for part in _LICENSE_SEPARATORS.split(cell) if clean(part)), None)
    if first is None or _PROVIDER_NUMBER.match(first):
        return None
    written = _LICENSE_STATE.search(first)
    unlabelled = _LICENSE_LABEL.sub("", _LICENSE_STATE.sub(" ", first).strip())
    number = clean(_LICENSE_STATUS.sub("", unlabelled))
    if number is None or number.upper() in _NO_LICENSE or not any(c.isalnum() for c in number):
        return None
    state = written[1].upper() if written else _licence_state(authority)
    return License(number=number, state=state)


def _action(text: str | None) -> ExclusionAction:
    """An exclusion when the sanctioning authority is the OIG, otherwise a sanction."""
    return ExclusionAction.EXCLUSION if _OIG.search(text or "") else ExclusionAction.SANCTION


def _reinstated_date(text: str | None, issues: RowIssues) -> date | None:
    """The end date in a ``Reinstated Date`` cell: a date alone or within a text.

    Blank, "Indefinite" and words without a date ("Revoked", "Active") have none; digits that
    form no date are counted as unreadable.
    """
    value = clean(text)
    if value is None or value.upper() in _OPEN_ENDED:
        return None
    found = _EMBEDDED_DATE.search(value)
    if found is None:
        if any(char.isdigit() for char in value):
            issues.unreadable[REINSTATED] += 1
        return None
    return open_ended(issues.date(REINSTATED, found[0], _DATE_FORMATS))


def _is_organization(text: str) -> bool:
    without = _PARENTHESIZED.sub(" ", text)
    words = {word.strip(",.;:").upper() for word in without.split()}
    return looks_like_business(without) or not words.isdisjoint(ORGANIZATION_WORDS)


def _credential(token: str, *, capitals_are_names: bool) -> bool:
    bare = token.replace(".", "").strip(",;:-")
    folded = bare.lower()
    if folded in SUFFIXES or folded in LOCAL_CREDENTIALS:
        return True
    if capitals_are_names or bare.upper() in BUSINESS_WORDS | _ALIAS_WORDS:
        return False
    return bool(_CAPITALS.match(bare.replace("-", "")))


def _role(token: str) -> bool:
    return token.strip(",.;:").casefold() in ROLE_WORDS


def _sorted_out(tokens: list[str], credentials: list[str], *, capitals_are_names: bool) -> bool:
    """Whether every token is a credential or a role; the credentials are collected."""
    if not tokens or not all(
        _credential(token, capitals_are_names=capitals_are_names) or _role(token)
        for token in tokens
    ):
        return False
    credentials.extend(
        token.strip(",;:")
        for token in tokens
        if _credential(token, capitals_are_names=capitals_are_names) and not _role(token)
    )
    return True


def _without_role_parentheses(
    text: str, credentials: list[str], *, capitals_are_names: bool
) -> str:
    """The text without parentheses holding only credentials or roles ("(PCA)", "(Owner)")."""

    def without_roles(found: re.Match[str]) -> str:
        tokens = [token for token in _TOKEN_SEPARATORS.split(found[1]) if token.strip(",")]
        sorted_out = _sorted_out(tokens, credentials, capitals_are_names=capitals_are_names)
        return " " if sorted_out else found[0]

    return collapse(_PARENTHESIZED.sub(without_roles, text)) or ""


def _name_part(text: str, credentials: list[str], *, capitals_are_names: bool) -> str:
    """One name of the cell without its credentials and roles; the credentials are collected."""
    collected: list[str] = []
    text = _without_role_parentheses(text, collected, capitals_are_names=capitals_are_names)
    pieces = [piece.strip() for piece in text.strip(_EDGE_PUNCTUATION).split(",")]
    trailing: list[str] = []
    if len(pieces) > 1 and len(pieces[0].split()) >= 2:
        trailing = [
            token.strip(",;:")
            for piece in pieces[1:]
            for token in _TOKEN_SEPARATORS.split(piece)
            if _credential(token, capitals_are_names=capitals_are_names)
        ]
        pieces = pieces[:1]
    while len(pieces) > 1:
        tokens = [token for token in _TOKEN_SEPARATORS.split(pieces[-1]) if token]
        found: list[str] = []
        if tokens and not _sorted_out(tokens, found, capitals_are_names=capitals_are_names):
            break
        trailing[:0] = found
        pieces.pop()
    words = pieces[-1].split()
    minimum = 2 if len(pieces) == 1 else 1
    while len(words) > minimum:
        found = []
        tokens = [token for token in _TOKEN_SEPARATORS.split(words[-1]) if token]
        if not _sorted_out(tokens, found, capitals_are_names=capitals_are_names):
            break
        trailing[:0] = found
        words.pop()
    credentials.extend([*collected, *trailing])
    return ", ".join([*pieces[:-1], " ".join(words)]).strip(_EDGE_PUNCTUATION)


def _kept(aliases: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _effective_date(row: Mapping[str, str], issues: RowIssues) -> date | None:
    return issues.date(EFFECTIVE, collapse(row[EFFECTIVE]), _DATE_FORMATS)


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row[NAME])
    if party.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    authority = clean(row[AUTHORITY])
    npis = parse_npi_list(row["NPI #"])
    effective_date = _effective_date(row, issues)
    end_date = _reinstated_date(row[REINSTATED], issues)
    if end_date is not None and effective_date is not None and end_date < effective_date:
        issues.unreadable[REINSTATED] += 1
        end_date = None
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        license=_license(row[LICENSE], authority),
        action=_action(authority),
        action_raw=authority,
        effective_date=effective_date,
        end_date=end_date,
        reason=clean(row[COMMENTS]),
        raw={column: value for column, value in row.items() if column not in NOT_STORED},
        valid_from=snapshot.fetched_at,
    )
