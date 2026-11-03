"""Connector for the Michigan MDHHS Sanctioned Provider List (Medicaid).

Source facts (survey of 2026-10-16 to 2026-10-22, re-checked 2026-11-02):

- Landing page: https://www.michigan.gov/mdhhs/doing-business/providers/providers/
  billingreimbursement/list-of-sanctioned-providers (Sitecore, "List of Sanctioned
  Providers", about 730 KB). It links the workbook as "List of Sanctioned Providers (XLSX)" at
  ``/mdhhs/-/media/Project/Websites/mdhhs/Assistance-Programs/Medicaid-BPHASA/MI-Sanctioned-
  Provider-List/MI_Sanctioned_Provider_List-XLSX.xlsx?rev=<32 hex>&hash=<32 hex>`` (and the
  same list as a PDF). The path is stable but the ``rev`` and ``hash`` query changes with
  every edition, so change signal ``LINK_DISCOVERY`` on the full URL, query included: one GET
  of the landing page per poll, a download only when the link changes. Polled weekly with a
  10 s pause before every request (our choice; robots.txt was unreadable to scripts).
- Akamai fronts the site. In the survey (2026-10-16 to 2026-10-22) it answered scripts and
  headless Chrome ``403`` (robots.txt too) and let headed Chrome through. On 2026-11-02 plain
  requests with the standard User-Agent got ``200`` for both the landing page and the file,
  and headed Chrome read the same bytes. ``browser_fallback`` repeats a refused request in
  headed Chrome.
- The file (2026-11-02): xlsx, 376,565 bytes, ``Content-Disposition: attachment``, ETag and
  Last-Modified (Fri, 30 Oct 2026), ``Cache-Control: public, max-age=3600``. One sheet,
  "Michigan": a title cell ("Medicaid Michigan Sanction Provider List", the edition date and
  a contact address), then the header ``Entity Name, Last Name, First Name, Middle Name,
  Provider Category, NPI#, City, License#, Sanction Date1, Sanction Source, Sanction Date2,
  Sanction Source, Reason`` (the second ``Sanction Source`` is read as ``Sanction Source
  (2)``). 4,109 rows.
- Parties: a row with ``Entity Name`` is that organization; a person named on the same row
  (285 rows: pharmacies, groups and agencies with their owners or officers) is a related
  party, written "LAST, First Middle". The provider category, the licence (a pharmacy
  licence or an NCPDP number) and the NPI on those rows describe the business, and 100 of
  those people also have a row of their own, while only 7 of the businesses do. A row
  without ``Entity Name`` is the person in the name columns. Name cells may hold one person
  per line on a business's row (lines paired across the name columns when their counts
  agree); "A/B" in a first name gives the other given name; a parenthesized part of a last
  name is a former surname ("LAST (MAIDEN)" gives the alias "MAIDEN, First Middle") and of a
  first name another given name ("First (Nick)" gives "LAST, Nick"). Generational suffixes
  ("JR", ", JR.", "III") and a credential after a comma ("LAST, MD") become the suffix.
  Business names may carry aliases ("... PC DBA ...", "... (AKA ...)").
- ``Provider Category`` is free text (Home Help 2,608 rows, Physician 529, Pharmacy 154, ...).
- ``NPI#`` is a number, or text padded with non-breaking spaces; one NPI per cell.
- ``License#``: mostly a ten-digit Michigan LARA number stored as a number; some text:
  "NCPDP 1234567" (a pharmacy's NCPDP number, not a licence), CLIA numbers ("12D3456789",
  not a licence), "-MN" after a Minnesota number, "(CS)" after a controlled-substance
  licence, two licences in one cell, zero-width spaces, and two numbers Excel turned into
  decimals ("43.012345"), which are not read. The first number of four or more digits (with
  an optional letter prefix) is the licence, Michigan's unless a "-XX" state follows.
- ``Sanction Date1`` is an Excel date on most rows and text (with stray spaces and line
  breaks, M/D/YYYY) on a few; it is the record's effective date. ``Sanction Date2`` and the
  second ``Sanction Source`` record later sanctions, often several dates in one cell, and
  stay in ``raw``, as does ``Sanction Source`` (MDHHS, MDHHS-OIG, LARA, DCH, HHS, ...).
- ``Reason`` (filled on 1,588 rows) is the source's wording of the actions ("Summary
  Suspension\\nTerminated", "1128(a)(1)", "Voluntarily Surrendered") and is the record's
  ``action_raw`` and reason. The action is the strongest one it names: an exclusion (an
  "1128" statute section or "Exclu..."), else a termination, else a suspension, else (a
  licence revocation, a surrender, an empty cell) a ``SANCTION``, the list's own term.
- ``City`` sometimes ends with another state ("Toledo, OH"); the state is read only then.
- No end date: "When the sanctioning body enddates a provider's sanction, that provider is
  removed" from the list (landing page), so removal is the only reinstatement signal.
- No date of birth, SSN or DEA column.
- Rows repeating one identity: a business listed once per owner or officer (same name, NPI,
  licence, date and action). They are merged into one record whose related parties are all
  those people, the differing raw columns joined with "; " (``JOINED``).
- First fetch 2026-11-02 (edition of 2026-10-29, read end to end with the connector, plain
  HTTP, no browser needed): 4,109 rows, 4,093 records, none skipped, 16 rows merged into 13
  businesses' records; 3,540 individuals and 553 organizations; NPI on 1,165, licence on 992
  (Michigan 990, Minnesota 2), city on 4,087 (another state named on 55), reason on 1,572,
  aliases on 14, related parties on 273, suffix on 53; actions: sanction 2,645, suspension
  624, termination 486, exclusion 338. Eight ``Sanction Date1`` values are unreadable
  ("12/31/202"-like typos), counted and left empty, three are empty.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    ParsedName,
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
    Address,
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)

ENTITY_NAME: Final = "Entity Name"
LAST_NAME: Final = "Last Name"
FIRST_NAME: Final = "First Name"
MIDDLE_NAME: Final = "Middle Name"
PROVIDER_CATEGORY: Final = "Provider Category"
NPI: Final = "NPI#"
CITY: Final = "City"
LICENSE: Final = "License#"
SANCTION_DATE: Final = "Sanction Date1"
SANCTION_SOURCE: Final = "Sanction Source"
LATER_SANCTION_DATE: Final = "Sanction Date2"
LATER_SANCTION_SOURCE: Final = "Sanction Source (2)"
REASON: Final = "Reason"
COLUMNS: Final = (
    ENTITY_NAME,
    LAST_NAME,
    FIRST_NAME,
    MIDDLE_NAME,
    PROVIDER_CATEGORY,
    NPI,
    CITY,
    LICENSE,
    SANCTION_DATE,
    SANCTION_SOURCE,
    LATER_SANCTION_DATE,
    LATER_SANCTION_SOURCE,
    REASON,
)
JOINED: Final = (
    LAST_NAME,
    FIRST_NAME,
    MIDDLE_NAME,
    PROVIDER_CATEGORY,
    CITY,
    SANCTION_SOURCE,
    LATER_SANCTION_DATE,
    LATER_SANCTION_SOURCE,
    REASON,
)
"""Raw columns kept in full when rows of one identity are merged (see the module notes)."""

ACTION_KEYWORDS: Final = (
    ("1128", ExclusionAction.EXCLUSION),
    ("exclu", ExclusionAction.EXCLUSION),
    ("terminat", ExclusionAction.TERMINATION),
    ("suspen", ExclusionAction.SUSPENSION),
)
"""Keywords of ``Reason``, strongest action first; anything else is a ``SANCTION``."""

_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "MI"
_FILE_LINK: Final = re.compile(
    r"/-/media/[^?#]*Sanctioned_Provider_List[^/?#]*\.xlsx(?:\?[^#]*)?$", re.IGNORECASE
)
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")
_NOT_LICENSES: Final = re.compile(
    r"NCPDP\s*#?:?\s*\d+|(?<![A-Za-z0-9])\d{2}D\d{7}(?![A-Za-z0-9])", re.IGNORECASE
)
"""Identifiers in ``License#`` that are not licences: NCPDP pharmacy numbers, CLIA numbers."""
_DECIMAL: Final = re.compile(r"^\d+\.\d+$")
_LICENSE_NUMBER: Final = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>[A-Z]{0,3}\d{4,}(?:-\d+)?)(?![A-Za-z0-9])"
    r"(?:\s*-\s*(?P<state>[A-Z]{2})(?![A-Za-z]))?"
)
_CITY_STATE: Final = re.compile(r"^(?P<city>.+?)\s*,\s*(?P<state>[A-Za-z]{2})\.?$")
_INVISIBLE: Final = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"))

LANDING_URL: Final = (
    "https://www.michigan.gov/mdhhs/doing-business/providers/providers/billingreimbursement/"
    "list-of-sanctioned-providers"
)
"""The MDHHS page that links the current workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="mi_mdhhs",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Michigan MDHHS Sanctioned Provider List",
    provider_type_fields=(PROVIDER_CATEGORY,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
)


class MiMdhhsConnector(BulkListConnectorBase):
    """Finds the current workbook on the MDHHS page, downloads it and maps each row."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "List of Sanctioned Providers (XLSX)" link, with its edition query."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the sheet; rows repeating one identity are merged."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=JOINED)
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


@dataclass(frozen=True, slots=True)
class ListedPerson:
    """A person in the name columns, with the other names the cells give for them."""

    name: PersonName
    other_names: tuple[str, ...] = ()

    def names(self) -> tuple[str, ...]:
        """The person written "LAST, First Middle", then their other names."""
        return (written(self.name), *self.other_names)


def written(name: PersonName) -> str:
    """A person's name as one text: "LAST, First Middle Suffix"."""
    given = " ".join(part for part in (name.first, name.middle, name.suffix) if part)
    return f"{name.last}, {given}" if given else name.last


def parse_party(row: Mapping[str, str]) -> ParsedName:
    """The row's party: the business in ``Entity Name`` with the people on its row as related
    parties, otherwise the person in the name columns (module rules)."""
    people = parse_people(row[LAST_NAME], row[FIRST_NAME], row[MIDDLE_NAME])
    entity = collapse(row[ENTITY_NAME])
    if entity is not None:
        organization, aliases = parse_organization(entity)
        related = (name for person in people for name in person.names())
        return ParsedName(
            person=None,
            organization=organization,
            aliases=aliases,
            related_parties=tuple(dict.fromkeys(related)),
        )
    if not people:
        raise ParseError("row has neither an entity name nor a last name")
    person, *others = people
    return ParsedName(
        person=person.name,
        organization=None,
        aliases=person.other_names,
        related_parties=tuple(dict.fromkeys(n for other in others for n in other.names())),
    )


def parse_organization(text: str) -> tuple[str, tuple[str, ...]]:
    """A business cell's name and aliases; a name that reads like a person's stays a business."""
    listed = parse_name_field(text, organization_hint=True)
    if listed.organization is not None:
        return listed.organization, listed.aliases
    primary = split_aliases(_PARENTHESIZED.sub(" ", text))[0] or text
    return primary, tuple(alias for alias in listed.aliases if alias != primary)


def parse_people(last: str, first: str, middle: str) -> tuple[ListedPerson, ...]:
    """The people in the name columns: one per line when the last and first names have the
    same number of lines, otherwise one person with the cells read whole."""
    lasts = _lines(last)
    firsts = _lines(first)
    if not lasts:
        return ()
    if len(lasts) == 1 or len(firsts) != len(lasts):
        return (_person(last, first, middle),)
    middles = _lines(middle)
    paired = middles if len(middles) == len(lasts) else [middle, *[""] * (len(lasts) - 1)]
    return tuple(_person(*names) for names in zip(lasts, firsts, paired, strict=True))


def _person(last_text: str, first_text: str, middle_text: str) -> ListedPerson:
    former = [name for name in map(collapse, _PARENTHESIZED.findall(last_text)) if name]
    last_cell = collapse(_PARENTHESIZED.sub(" ", last_text))
    if last_cell is None:
        raise ParseError("a person has no last name")
    last, suffix = split_suffix(last_cell)
    other_given = [name for name in map(collapse, _PARENTHESIZED.findall(first_text)) if name]
    given_names = [
        name for name in map(collapse, _PARENTHESIZED.sub(" ", first_text).split("/")) if name
    ]
    first = given_names[0] if given_names else ""
    name = PersonName(last=last, first=first, middle=collapse(middle_text), suffix=suffix)
    given = " ".join(part for part in (name.first, name.middle) if part)
    aliases = [f"{surname}, {given}" if given else surname for surname in former]
    aliases += [f"{last}, {other}" for other in (*given_names[1:], *other_given)]
    return ListedPerson(name=name, other_names=tuple(dict.fromkeys(aliases)))


def _lines(text: str) -> list[str]:
    return [line for line in (collapse(part) for part in text.splitlines()) if line]


def parse_license(text: str | None) -> License | None:
    """The first licence number in ``License#``, Michigan's unless a "-XX" state follows it;
    NCPDP and CLIA numbers and decimals are not licences (module rules)."""
    value = (text or "").translate(_INVISIBLE)
    if _DECIMAL.match(value.strip()):
        return None
    found = _LICENSE_NUMBER.search(_NOT_LICENSES.sub(" ", value).replace(".", " "))
    if found is None:
        return None
    return License(number=found["number"], state=found["state"] or _STATE)


def parse_city(text: str | None) -> Address | None:
    """The address in ``City``: the city, and a state only when the cell ends with one."""
    value = collapse(text)
    if value is not None and (found := _CITY_STATE.match(value)):
        return parse_address(city=found["city"], state=found["state"])
    return parse_address(city=value)


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row)
    npis = parse_npi_list(row[NPI])
    reason = clean(row[REASON])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        related_parties=party.related_parties,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        license=parse_license(row[LICENSE]),
        address=parse_city(row[CITY]),
        action=classify_action(reason, ACTION_KEYWORDS),
        action_raw=reason,
        effective_date=issues.date(SANCTION_DATE, row[SANCTION_DATE], _DATE_FORMATS),
        reason=reason,
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
