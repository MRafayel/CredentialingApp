"""Connector for the California DHCS Medi-Cal Suspended and Ineligible Provider List.

Source facts (survey of 2026-10-16, re-checked and first sync 2026-10-30):

- Published on the CHHS open-data portal (CKAN), resource
  ``48630a37-b5ba-4d3d-af54-e82b30e658a0`` of package ``8f70e05d-baa5-4296-a5ab-36318d530670``;
  the people's page is the resource page. ``accrual_periodicity`` is monthly.
- ``GET /api/3/action/resource_show?id=<resource>`` (1.7 KB of JSON) gives the file's ``url``,
  ``last_modified`` (UTC without an offset), ``hash`` (the MD5 of the file, verified) and
  ``size``. robots.txt disallows ``/api/``; the owner accepted using this API (decision of
  2026-10-22), the resource page being the fallback. robots.txt also asks ``Crawl-delay:
  10``, which every request honours. Change signal ``API_METADATA``: one metadata request per
  poll, polled daily; the file is downloaded only when the hash changes, and must hash to it.
- The file ``url`` changes monthly (``.../download/suspended-ineligible-list-september-2026.csv``
  on 2026-10-30, uploaded 2026-10-12) and answers ``302`` to a presigned S3 URL valid 24 h.
  The snapshot keeps the CKAN URL, not the presigned one.
- CSV, UTF-8 with a byte order mark, CRLF, every row 11 fields: ``Last Name, First Name,
  Middle Name, A/K/A-Also Known As\\nD/B/A-Doing Business as`` (a line break inside the
  header), ``Address(es), Provider Type, License Number, Provider Number, Date of Suspension,
  Active Period`` and an unnamed empty column (dropped from ``raw``). 23,526 rows, 3,392,689
  bytes. Placeholders "N/A", "n/a", "NA", "Na".
- Organizations have "N/A" (or nothing) as ``First Name`` and the business name in ``Last
  Name``. A person's generational suffix follows a comma in ``Last Name`` ("LAST, Jr.").
- The alias column holds several names separated by ";" or line breaks. On a person's row
  they are former and other names of the person (aliases); an unmarked business name there
  is another party (related party), and one marked "dba" or "aka" an alias. On an
  organization's row a business name is another name of the business (alias) and a person's
  name, typically the owner ("Provider Type" "...; Owner"), a related party.
- ``Address(es)``: several addresses separated by ";", one address sometimes over two lines.
  The first is stored ("street, city, ST ZIP"; often only "city, ST").
- ``Provider Number`` has no NPI column of its own: it mixes NPIs (ten digits with a valid
  check digit) and Medi-Cal provider numbers, several per cell, separated by commas,
  semicolons or line breaks. The first NPI is the record's NPI, further NPIs related NPIs,
  and the first other number the state provider ID; the rest stay in ``raw``.
- ``License Number``: the first is kept, as a California licence. ``Date of Suspension`` is
  ``M/D/YYYY``. ``Active Period`` is "Indefinitely effective" on every row, so a suspension has
  no end date.
- Some rows repeat another row's identity (same name, numbers, licence and date): 45 groups,
  47 extra rows on 2026-10-30. 25 are exact repeats and one differs only in spacing; the
  others differ in provider type (10 groups), address (10) or alias (1), and three of them also
  spell the date two ways ("1/2/2020", "01/02/2020"). Rows with the same identity are merged
  into one record whose provider types, aliases and addresses are joined with "; " in file
  order; the first address is the structured one.
- First sync 2026-10-30: 23,526 rows, 23,479 records, 47 merged, none skipped: 22,200
  individuals and 1,279 organizations, NPI on 3,364 records (a related NPI on 248), a Medi-Cal
  number on 3,846, licence on 17,533, address on 23,478, aliases on 5,011 and related parties
  on 323. Fourteen organization rows are professional corporations named after one person
  ("FIRST LAST MD, Inc."); they stay organizations, as the empty first name says. A second
  poll was the one metadata request ("metadata unchanged"), after the 10-second crawl delay.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from credentialing.connectors.framework.base import BulkListConnectorBase, Metadata
from credentialing.connectors.framework.delimited import read_delimited
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import SUFFIXES, parse_name_lines
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.values import (
    clean,
    collapse,
    parse_city_state_zip,
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
from credentialing.domain.models import (
    Address,
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)
from credentialing.domain.types import validate_npi

ALIASES: Final = "A/K/A-Also Known As\nD/B/A-Doing Business as"
PROVIDER_TYPE: Final = "Provider Type"
ADDRESSES: Final = "Address(es)"
COLUMNS: Final = (
    "Last Name",
    "First Name",
    "Middle Name",
    ALIASES,
    ADDRESSES,
    PROVIDER_TYPE,
    "License Number",
    "Provider Number",
    "Date of Suspension",
    "Active Period",
    "",
)
_DATE_FORMATS: Final = ("%m/%d/%Y",)
_STATE: Final = "CA"
_NAME_SEPARATORS: Final = re.compile(r"[;\r\n]+")
_LINE_BREAKS: Final = re.compile(r"\s*[\r\n]+\s*")

PACKAGE_ID: Final = "8f70e05d-baa5-4296-a5ab-36318d530670"
RESOURCE_ID: Final = "48630a37-b5ba-4d3d-af54-e82b30e658a0"
METADATA_URL: Final = f"https://data.chhs.ca.gov/api/3/action/resource_show?id={RESOURCE_ID}"
"""The CKAN request describing the current file."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ca_dhcs",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.API_METADATA,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=1),
    source_url=f"https://data.chhs.ca.gov/dataset/{PACKAGE_ID}/resource/{RESOURCE_ID}",
    display_name="California Medi-Cal Suspended and Ineligible Providers",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
)


class _Resource(BaseModel):
    url: str = Field(min_length=1)
    hash: str = Field(pattern=r"^[0-9A-Fa-f]{32}$")
    last_modified: datetime | None = None


class _ResourceShow(BaseModel):
    success: bool
    result: _Resource


class CaDhcsConnector(BulkListConnectorBase):
    """Asks CKAN for the list's current file, downloads it on a new hash and maps each row."""

    metadata_url = METADATA_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def read_metadata(self, body: bytes) -> Metadata:
        """The file's URL, MD5 and upload time from a ``resource_show`` answer."""
        try:
            answer = _ResourceShow.model_validate_json(body)
        except ValidationError as error:
            raise ParseError("not a CKAN resource_show answer") from error
        if not answer.success:
            raise ParseError("CKAN reported the request as unsuccessful")
        modified = answer.result.last_modified
        if modified is not None and modified.tzinfo is None:
            modified = modified.replace(tzinfo=UTC)
        return Metadata(
            url=answer.result.url,
            hash=answer.result.hash,
            last_modified=modified.astimezone(UTC) if modified else None,
        )

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row into a suspension record; rows repeating an identity are merged."""
        rows = read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=(ALIASES, ADDRESSES, PROVIDER_TYPE))
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


@dataclass(frozen=True, slots=True)
class Party:
    """A row's party: a person from the name columns or an organization from ``Last Name``,
    with the aliases and related parties the alias column names."""

    person: PersonName | None
    organization: str | None
    aliases: tuple[str, ...]
    related_parties: tuple[str, ...]


def parse_party(row: Mapping[str, str]) -> Party:
    """The row's party; a row without a first name is an organization's, as written."""
    last = collapse(row["Last Name"])
    if last is None:
        raise ParseError("row has no last or business name")
    first = clean(row["First Name"])
    others = [line for line in _NAME_SEPARATORS.split(row[ALIASES]) if clean(line)]
    if first is None:
        parsed = parse_name_lines([last, *others], organization_hint=lambda line: line == last)
        organization = parsed.organization or last
        return Party(
            person=None,
            organization=organization,
            aliases=tuple(alias for alias in parsed.aliases if alias != organization),
            related_parties=parsed.related_parties,
        )
    last, suffix = split_suffix(last)
    middle = clean(row["Middle Name"])
    written = collapse(f"{last}, {first} {middle or ''}") or last
    parsed = parse_name_lines([written, *others])
    return Party(
        person=PersonName(last=last, first=first, middle=middle, suffix=suffix),
        organization=None,
        aliases=parsed.aliases,
        related_parties=parsed.related_parties,
    )


def split_suffix(last_name: str) -> tuple[str, str | None]:
    """ "LAST, Jr." as ``("LAST", "Jr.")``; a last name without a suffix after a comma as is."""
    head, comma, tail = last_name.partition(",")
    words = tail.replace(",", " ").split()
    if comma and head.strip() and words and all(_is_suffix(word) for word in words):
        return head.strip(), " ".join(words)
    return last_name, None


def first_address(text: str | None) -> Address | None:
    """The first of the ";"-separated addresses, split into street, city, state and ZIP."""
    first = next((part for part in (text or "").split(";") if clean(part)), None)
    value = clean(_LINE_BREAKS.sub(", ", first or ""))
    if value is None:
        return None
    parsed = parse_city_state_zip(value)
    if parsed is None or parsed.line1 is not None or parsed.city is None:
        return parsed
    street, comma, city = parsed.city.rpartition(",")
    if not comma:
        return parsed
    return parsed.model_copy(update={"line1": street.strip(" ,") or None, "city": city.strip()})


def split_provider_numbers(text: str | None) -> tuple[tuple[str, ...], str | None]:
    """The valid NPIs in a ``Provider Number`` cell and the first other provider number."""
    numbers = [value for value in map(clean, split_identifiers(text)) if value]
    others = [number for number in numbers if not validate_npi(number)]
    return parse_npi_list(text), others[0] if others else None


def _is_suffix(word: str) -> bool:
    return word.replace(".", "").lower() in SUFFIXES


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row)
    entity_kind = EntityKind.INDIVIDUAL if party.person is not None else EntityKind.ORGANIZATION
    npis, state_provider_id = split_provider_numbers(row["Provider Number"])
    license_number = next(
        (value for value in map(clean, split_identifiers(row["License Number"])) if value), None
    )
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        related_parties=party.related_parties,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=state_provider_id,
        license=License(number=license_number, state=_STATE) if license_number else None,
        address=first_address(row[ADDRESSES]),
        action=ExclusionAction.SUSPENSION,
        effective_date=issues.date("Date of Suspension", row["Date of Suspension"], _DATE_FORMATS),
        raw={column: value for column, value in row.items() if column},
        valid_from=snapshot.fetched_at,
    )
