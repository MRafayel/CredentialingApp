"""Connector for the Georgia DCH Office of Inspector General exclusions list.

Source facts (survey of 2026-10-16, re-checked and first sync 2026-10-30):

- Landing page: https://dch.georgia.gov/office-inspector-general/georgia-oig-exclusions-list
  ("updated at least monthly"; Drupal, robots.txt disallows only admin and user paths, no
  crawl delay). It links the workbook as "List of Excluded Individuals" at
  ``/document/document/<slug with the release date>/download``
  (``...-excluded-individuals-and-entities-october-5-2026/download`` on 2026-10-30); the URL
  changes with every release, so change signal ``LINK_DISCOVERY``: one GET of the landing
  page per poll, a download only when the link changes. The landing page's own validators
  change more often than the file. Polled weekly.
- The download answers ``200`` directly (no redirect), Last-Modified and no ETag;
  ``If-Modified-Since`` gives ``304`` on a given URL. 605,486 bytes on 2026-10-30.
- Four sheets; the data is on the first ("Sheet1"): a title cell ("... as of 10/05/2026
  *Highlighted individuals recently added*"), a blank row, then the header on row 3:
  ``LAST NAME, FIRST NAME, MIDDLE NAME, BUSINESS NAME, GENERAL, STATE, SANCDATE, NPI, DOB``.
  1,377 rows. "Recently added" is shown only by cell highlighting, which is not read.
- ``SANCDATE`` is ``YYYYMMDD``, as text or as an integer, and an Excel date on a few rows.
  ``NPI`` is filled on 38 rows. ``DOB`` on 2 (an Excel date): stored as the record's date of
  birth for matching (never logged, never shown by the API). ``GENERAL`` is the provider's
  type or role ("MEDICAL PRACTICE, MD", "PRIVATE CITIZEN"). ``STATE`` lists one or more states
  ("GA", "AL, GA") and stays in ``raw``.
- A row is a person when ``LAST NAME`` is set, otherwise the organization ``BUSINESS NAME``.
  Eight rows have both: the business is another party on the person's row (a related party),
  not an alias. A former name in parentheses after the last name ("LAST (MAIDEN)") becomes
  the alias "MAIDEN, FIRST".
- Removal from the list is the only reinstatement signal: a removed row closes its record.
- First sync 2026-10-30: 1,377 rows and records, none skipped, 1,376 distinct (one row is
  repeated): 1,277 individuals and 100 organizations, NPI on 38, date of birth on 2, three
  former-name aliases, eight related businesses. Five ``SANCDATE`` values are written
  day before month (``2022DDMM`` with the month position above 12) and are counted
  unreadable, the date left empty, rather than guessed. A second poll was one request.
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import collapse, parse_npi_list
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
    PersonName,
    SourceSnapshot,
)

COLUMNS: Final = (
    "LAST NAME",
    "FIRST NAME",
    "MIDDLE NAME",
    "BUSINESS NAME",
    "GENERAL",
    "STATE",
    "SANCDATE",
    "NPI",
    "DOB",
)
_DATE_FORMATS: Final = ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "GA"
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")
_FILE_LINK: Final = re.compile(r"/document/document/[^/?#]*exclu[^/?#]*/download$")

LANDING_URL: Final = "https://dch.georgia.gov/office-inspector-general/georgia-oig-exclusions-list"
"""The page that links the current workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ga_dch_oig",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Georgia OIG Excluded Individuals and Entities",
    provider_type_fields=("GENERAL",),
)


class GaDchConnector(BulkListConnectorBase):
    """Finds the current workbook on the DCH page, downloads it and maps each row."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "List of Excluded Individuals" document's download link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the first sheet into an exclusion record."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def split_former_names(last_name: str) -> tuple[str | None, tuple[str, ...]]:
    """A last name without its parenthesized former names, and those names."""
    others = tuple(name for name in map(collapse, _PARENTHESIZED.findall(last_name)) if name)
    return collapse(_PARENTHESIZED.sub(" ", last_name)), others


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    last_cell = collapse(row["LAST NAME"])
    business = collapse(row["BUSINESS NAME"])
    person: PersonName | None = None
    organization: str | None = None
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    if last_cell is not None:
        last, former = split_former_names(last_cell)
        first = collapse(row["FIRST NAME"]) or ""
        entity_kind = EntityKind.INDIVIDUAL
        person = PersonName(
            last=last or last_cell, first=first, middle=collapse(row["MIDDLE NAME"])
        )
        aliases = tuple(f"{name}, {first}" if first else name for name in former)
        related = (business,) if business else ()
    elif business is not None:
        listed = parse_name_field(business, organization_hint=True)
        entity_kind = EntityKind.ORGANIZATION
        organization = listed.organization or business
        aliases = listed.aliases if listed.organization else ()
    else:
        raise ParseError("row has neither a last name nor a business name")
    npis = parse_npi_list(row["NPI"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=entity_kind,
        person_name=person,
        organization_name=organization,
        aliases=aliases,
        related_parties=related,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        date_of_birth=issues.date("DOB", row["DOB"], _DATE_FORMATS),
        action=ExclusionAction.EXCLUSION,
        effective_date=issues.date("SANCDATE", row["SANCDATE"], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
