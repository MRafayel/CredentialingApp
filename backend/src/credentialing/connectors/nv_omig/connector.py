"""Connector for the Nevada OMIG "Nevada Medicaid Sanctions / Office of Inspector General
Exclusions List".

Owner decision (2026-10-31): Nevada's robots.txt is ``Disallow: /`` for every agent except
two named crawlers. The owner decided to fetch this list anyway, as politely as possible:
weekly polling, a 10-second pause before every request (``crawl_delay``, our own choice; the
site declares none), one landing-page GET per poll, the 7 MB PDF downloaded only when its
link changes, no retries beyond the fetcher's single one, and the configured browser
User-Agent.

Source facts (survey of 2026-10-19, re-checked and first sync 2026-10-31):

- Landing page: https://www.omig.nv.gov/provider-enrollment--termination/provider-exclusions-sanctions-and-press-releases/
  (35 KB). Its "NV Exclusion List" button links the PDF at
  ``/siteassets/press-releases/master-exclusions-<M.D.YYYY>.pdf`` (``...-10.21.2026.pdf`` on
  2026-10-31); the name carries the edition date, so change signal ``LINK_DISCOVERY``. The
  file (7,076,663 bytes) has an ETag and Last-Modified. The page says the list is "updated
  periodically". Polled weekly.
- 36 landscape pages (1008 x 612 pt), an Excel export with a ruled grid and the header
  repeated on every page: ``Excluded Providers/Entities and/or Individuals, Sanctioned/
  Excluded NPI, Medicaid Provider, Provider Type, Associated Legal Entity, Persons with
  controlling interest of 5% or more, Contract Termination Date, Nevada Medicaid Sanction
  Tier, Nevada Medicaid Sanction Period, Nevada Medicaid Sanction Period End Date, OIG
  Exclusion Date, OIG Reinstate Date``. 1,320 rows (one row's grid is ruled only in its last
  two columns; the reader reads its other cells from the page); wrapped cells keep their line
  breaks.
- The name is free text, first name first, a person or an entity ("First M. Last MD",
  "First Last aka Other Name", "Sample Care LLC dba Sample Center"); a line break in it is
  wrapping. ``Medicaid Provider`` ("X") and ``Provider Type`` (numeric codes, "30, 48")
  stay in ``raw``; neither says person or entity reliably. A name the file lists as some
  row's ``Associated Legal Entity`` (and never as a person with controlling interest) is
  read as an organization unless it ends in a credential: two-word entity names have no
  business word to tell them apart from a person.
- An entity and each person with a controlling interest have their own rows, each naming
  the other: ``Associated Legal Entity`` (one name, wrapped, with its dba names) and
  ``Persons with controlling interest`` (one name per line) are other parties, stored as
  related parties split at aka/dba markers, never as aliases.
- ``Sanctioned/Excluded NPI`` holds one number per line: check-digit-valid NPIs become the
  NPI and related NPIs; ten-digit numbers starting with 9 (all seen start 9005) are Nevada
  Medicaid provider IDs, the first stored as the state provider ID. Other numbers (five
  NPI-like values with a wrong check digit) stay in ``raw``.
- Dates are ``M/D/YYYY``. One termination cell holds two dates (the first is used) and two
  period end dates carry a trailing "*" (ignored); the cells stay in ``raw``.
- Action: tier "Federal" rows are OIG exclusions (``EXCLUSION``, effective on the OIG
  exclusion date, else the contract termination date; ending on the OIG reinstate date). A
  numeric tier ("1", "2", "3", "4", "7"; "2\\n3" read "Tier 2, 3") is a Nevada Medicaid
  ``SANCTION`` effective on the contract termination date, ending on the sanction period end
  date unless the period is "Permanent" (four Permanent sanction records also carry an end
  date, which is ignored); the period text ("10 Years", "3 years", ...) is the reason. A
  row without a tier is an ``EXCLUSION`` when it has an OIG exclusion date, a ``SANCTION``
  when it has a termination date or a sanction period, and ``OTHER`` when nothing says what
  happened (23, 2 and 1 records on 2026-10-31).
- Two rows hold only a note in the name cell (a date and a sentence about the columns) and
  are skipped.
- An entity is repeated once per controlling person: rows of one identity are merged, every
  column but the name joined with "; " (83 identities over 100 extra rows on 2026-10-31; two
  groups disagree on the period end date, and the first row's date is kept).
- First sync 2026-10-31: 1,320 rows, 1,218 records, 2 skipped, 100 merged, no unreadable
  value: 805 individuals and 412 organizations; NPI on 834 (a related NPI on 3), Medicaid ID
  on 27; 872 sanctions, 344 exclusions (321 Federal), 1 other; end date on 688; related
  parties on 792, aliases on 66. Of the 403 records listing controlling persons, 381 are
  organizations.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import ParsedName, parse_name_field
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    UnreadableRow,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.values import (
    clean,
    collapse,
    parse_npi_list,
    split_identifiers,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord, SourceSnapshot
from credentialing.domain.names import normalize_name

NAME: Final = "Excluded Providers/Entities and/or Individuals"
NPIS: Final = "Sanctioned/Excluded NPI"
PROVIDER_TYPE: Final = "Provider Type"
LEGAL_ENTITY: Final = "Associated Legal Entity"
CONTROLLING: Final = "Persons with controlling interest of 5% or more"
TERMINATION: Final = "Contract Termination Date"
TIER: Final = "Nevada Medicaid Sanction Tier"
PERIOD: Final = "Nevada Medicaid Sanction Period"
PERIOD_END: Final = "Nevada Medicaid Sanction Period End Date"
OIG_EXCLUSION: Final = "OIG Exclusion Date"
OIG_REINSTATE: Final = "OIG Reinstate Date"
COLUMNS: Final = (
    NAME,
    NPIS,
    "Medicaid Provider",
    PROVIDER_TYPE,
    LEGAL_ENTITY,
    CONTROLLING,
    TERMINATION,
    TIER,
    PERIOD,
    PERIOD_END,
    OIG_EXCLUSION,
    OIG_REINSTATE,
)
JOINED: Final = COLUMNS[1:]
"""Raw columns whose distinct values are all kept when rows of one identity are merged: every
column but the name (whose copies differ at most in spacing)."""

_STATE: Final = "NV"
_DATE_FORMATS: Final = ("%m/%d/%Y",)
_FEDERAL: Final = "federal"
_PERMANENT: Final = "permanent"
_TIERS: Final = re.compile(r"^\d+(?:\s+\d+)*$")
_MEDICAID_ID: Final = re.compile(r"^9[0-9]{9}$")
_NAME_MARKER: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:a\.?k\.?a\.?|a/k/a|d\.?b\.?a\.?|d/b/a|f\.?k\.?a\.?|f/k/a)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_EDGE: Final = " ,;:-&"
_NOTE_DATE: Final = re.compile(r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}|\b(?:19|20)[0-9]{2}\b")
_FILE_LINK: Final = re.compile(r"/siteassets/press-releases/master-exclusions-[\d.]+\.pdf$")

LANDING_URL: Final = (
    "https://www.omig.nv.gov/provider-enrollment--termination/"
    "provider-exclusions-sanctions-and-press-releases/"
)
"""The page that links the current PDF."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="nv_omig",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Nevada Medicaid Sanctions and OIG Exclusions List",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
)


class NvOmigConnector(BulkListConnectorBase):
    """Finds the current PDF on the OMIG page, downloads it and maps each row."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "NV Exclusion List" PDF link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row into a record; rows repeating an identity are merged."""
        rows = list(read_pdf_table(body, expected_columns=COLUMNS))
        entities = legal_entity_names(row for row in rows if not isinstance(row, UnreadableRow))
        parsed = build_records(rows, partial(_record, snapshot, entities))
        records = merge_duplicates(parsed.records, joined=JOINED)
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


@dataclass(frozen=True, slots=True)
class Action:
    """What a row says happened: the normalized action, the source's wording and dates."""

    action: ExclusionAction
    action_raw: str | None
    effective_date: date | None
    end_date: date | None
    reason: str | None


def split_names(text: str | None) -> tuple[str, ...]:
    """The names in one cell, wrapping undone and cut at aka/dba markers, as written."""
    collapsed = collapse(text)
    if collapsed is None:
        return ()
    parts = (part.strip(_EDGE) for part in _NAME_MARKER.split(collapsed))
    return tuple(part for part in parts if part)


def controlling_persons(text: str | None) -> tuple[str, ...]:
    """The persons with a controlling interest: one per line, each line cut at aka markers."""
    return tuple(name for line in (text or "").splitlines() for name in split_names(line))


def legal_entity_names(rows: Iterable[Mapping[str, str]]) -> frozenset[str]:
    """Normalized names the file lists as an associated legal entity and never as a person
    with a controlling interest: the row of such a name is an entity's."""
    entities: set[str] = set()
    persons: set[str] = set()
    for row in rows:
        entities.update(normalize_name(name) for name in split_names(row[LEGAL_ENTITY]))
        persons.update(normalize_name(name) for name in controlling_persons(row[CONTROLLING]))
    return frozenset(entities - persons - {""})


def parse_party(row: Mapping[str, str], entities: frozenset[str]) -> ParsedName:
    """The row's party; a name another row lists as its legal entity hints an organization."""
    text = collapse(row[NAME])
    if text is None:
        raise ParseError("row has no name")
    if is_note(row):
        raise ParseError("row is a note, not a party")
    hint = any(normalize_name(name) in entities for name in split_names(text))
    party = parse_name_field(text, organization_hint=hint, first_name_first=True)
    if party.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no name")
    return party


def is_note(row: Mapping[str, str]) -> bool:
    """A row with only its name cell filled, and a date in it, is a note about the list."""
    others = any(clean(row[column]) for column in COLUMNS if column != NAME)
    return not others and bool(_NOTE_DATE.search(row[NAME]))


def related_parties(row: Mapping[str, str], party: str) -> tuple[str, ...]:
    """The associated legal entity and the controlling persons, without the row's party."""
    own = {normalize_name(name) for name in split_names(party)}
    related: dict[str, str] = {}
    for name in (*split_names(row[LEGAL_ENTITY]), *controlling_persons(row[CONTROLLING])):
        key = normalize_name(name)
        if key and key not in own:
            related.setdefault(key, name)
    return tuple(related.values())


def split_numbers(text: str | None) -> tuple[tuple[str, ...], str | None]:
    """The valid NPIs in the NPI cell and the first Nevada Medicaid provider ID."""
    numbers = [value for value in map(clean, split_identifiers(text)) if value]
    medicaid_ids = [number for number in numbers if _MEDICAID_ID.match(number)]
    return parse_npi_list(text), medicaid_ids[0] if medicaid_ids else None


def parse_action(row: Mapping[str, str], issues: RowIssues) -> Action:
    """The action, dates and reason of a row, following the tier (module rules)."""
    tier = collapse(row[TIER])
    period = collapse(row[PERIOD])
    terminated = _date(row, TERMINATION, issues)
    period_end = _date(row, PERIOD_END, issues)
    excluded = _date(row, OIG_EXCLUSION, issues)
    reinstated = _date(row, OIG_REINSTATE, issues)
    if (tier is not None and tier.casefold() == _FEDERAL) or (tier is None and excluded):
        return Action(ExclusionAction.EXCLUSION, tier, excluded or terminated, reinstated, None)
    if tier is None and terminated is None and period is None and period_end is None:
        return Action(ExclusionAction.OTHER, None, None, None, None)
    permanent = period is not None and period.casefold() == _PERMANENT
    return Action(
        action=ExclusionAction.SANCTION,
        action_raw=_tier_label(tier),
        effective_date=terminated,
        end_date=None if permanent else period_end,
        reason=period,
    )


def _tier_label(tier: str | None) -> str | None:
    if tier is None or not _TIERS.match(tier):
        return tier
    return f"Tier {', '.join(tier.split())}"


def _date(row: Mapping[str, str], column: str, issues: RowIssues) -> date | None:
    """The cell's first date; a footnote mark ("6/20/2017*") is ignored."""
    lines = row[column].strip().splitlines()
    text = lines[0].strip().rstrip("*").strip() if lines else ""
    return issues.date(column, text, _DATE_FORMATS)


def _record(
    snapshot: SourceSnapshot,
    entities: frozenset[str],
    row: Mapping[str, str],
    issues: RowIssues,
) -> ExclusionRecord:
    party = parse_party(row, entities)
    npis, medicaid_id = split_numbers(row[NPIS])
    action = parse_action(row, issues)
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        related_parties=related_parties(row, collapse(row[NAME]) or ""),
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=medicaid_id,
        action=action.action,
        action_raw=action.action_raw,
        effective_date=action.effective_date,
        end_date=action.end_date,
        reason=action.reason,
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
