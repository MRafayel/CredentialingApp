"""Connector for the MassHealth list of suspended or excluded providers (Massachusetts).

The list holds suspensions and exclusions effective on or after March 23, 2010, "listed
according to the provider type at the time of such action"; it is "updated monthly", posted
"around the second week of the month for the previous month". A provider leaves it only when
MassHealth approves a re-enrolment.

Source facts (survey of 2026-10-16 to 2026-10-22, re-checked 2026-11-02):

- Landing page: https://www.mass.gov/info-details/learn-about-suspended-or-excluded-masshealth-
  providers (Drupal behind Akamai; 186,246 bytes, ETag and Last-Modified). It links the same
  edition twice, as "List of suspended or excluded MassHealth providers (9/30/2026)": a PDF at
  ``/doc/list-of-suspended-or-excluded-masshealth-providers-9302026-0/download`` and the
  workbook at ``/doc/list-of-suspended-or-excluded-masshealth-providers-9302026/download``.
  The slug carries the edition date and Drupal's "-0" goes to whichever document came second,
  so the workbook is recognized by its link's own label (", Open XLSX file, 30.11 KB"), not by
  its URL. The URL changes with every edition: change signal ``LINK_DISCOVERY``, one landing
  GET per poll and a download only on a new link. Polled weekly with a 10-second crawl delay
  before every request (our choice: robots.txt answered scripts 403 in the survey and was
  not re-read).
- The workbook answers ``200`` on the same host with no redirect: Content-Disposition
  ``attachment; filename=suspended-excluded-mh-list_0.xlsx``, ETag and Last-Modified
  (2026-10-27), 30,836 bytes.
- Access: in the survey Akamai answered scripts (and robots.txt) with ``403`` while a real
  Chrome passed; on 2026-11-02 plain HTTP with the standard User-Agent got ``200`` for the
  landing page and the workbook. ``browser_fallback`` repeats a refused request in headed
  Chrome, so a returning block costs one browser run, never an empty list.
- Two sheets, header on the first row of each:
  "Exclusions List" ``Provider Name, Provider Type, National Provider Identifier (NPI),
  Unique ID, Suspension/Exclusion Reason, Suspension/Exclusion Effective Date``, 299 rows
  (one empty), and "Removed fx the list" ``Provider Name, Provider Type , National Provider
  Identifier (NPI), Suspension/Exclusion Reason, Suspension/Exclusion Effective Date,
  Reinstated into MassHealth Effective Date, Notes``, 9 rows of reinstated providers. Both
  sheets are read, by position; a workbook without either is a failed fetch.
- Names are one upper-case cell, "FIRST M LAST" for people, with double spaces on 26 rows.
  A comma sets off a designator ("..., LLC", "..., INC") or a suffix or credential
  ("FIRST M LAST, II", "FIRST LAST, MD PC"); before a suffix or credential it is dropped, so
  the name is read first name first and like its comma-less spelling.
- Person or organization: an organization when the name has a business word (LLC, INC, CARE,
  HEALTH, ...) or the provider type is an organization type (``ORGANIZATION_TYPES``: group
  practice, home health agency, pharmacy, transportation, ...) and the name carries no
  personal credential; otherwise a person. A group practice named after one person, with a
  credential and a designator ("FIRST LAST MD PC"), is that person with the practice as an
  alias (``names.py`` rule 2). A parenthesized part of a name is an alias (rule 1).
- ``Provider Type`` has 33 values (two written with a leading space, which the reader trims);
  it stays in ``raw``.
  ``National Provider Identifier (NPI)`` is a number, text, "NONE" or empty; every valid NPI
  is read. ``Unique ID`` (five or six digits) is set only on Personal Care Attendant rows,
  which have no NPI, and becomes the record's state provider ID.
- The list does not say which rows are suspensions and which exclusions, so every row is a
  ``SANCTION``; ``Suspension/Exclusion Reason`` (eight values such as "MEDICARE SUSPENSION",
  "LICENSE REVOKED BY LICENSING BOARD") is the record's reason, written as published
  (including "DECERTIFICAITON"). The effective date is an Excel date (2010-03-30 to
  2026-07-31); the reinstated sheet's ``Reinstated into MassHealth Effective Date`` is the
  record's end date, so those records read as ended.
- A provider suspended under several provider types has one row per type, identical
  otherwise: such rows are one record, the types (and any differing reasons) joined with "; ".
- First fetch 2026-11-02 (edition 9/30/2026, over plain HTTP, no browser needed): 307 rows
  (298 listed, 9 reinstated), none skipped, 303 records (4 rows merged into two records of
  one provider under several types): 221 individuals and 82 organizations, NPI on 248,
  Unique ID on 35, 9 ended (reinstated), 4 aliases (3 professional corporations, 1
  parenthesized name), no unreadable dates. Five organization names have no business word
  and are organizations by their provider type alone (3 group practices, 1 transportation,
  1 adult foster care).
"""

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from functools import partial
from html import unescape
from itertools import chain
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    CORPORATE_DESIGNATORS,
    holds_only_credentials,
    parse_name_field,
)
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import clean, collapse, parse_npi_list
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord, SourceSnapshot

PROVIDER_NAME: Final = "Provider Name"
PROVIDER_TYPE: Final = "Provider Type"
NPI: Final = "National Provider Identifier (NPI)"
UNIQUE_ID: Final = "Unique ID"
REASON: Final = "Suspension/Exclusion Reason"
EFFECTIVE_DATE: Final = "Suspension/Exclusion Effective Date"
REINSTATED: Final = "Reinstated into MassHealth Effective Date"
NOTES: Final = "Notes"

COLUMNS: Final = (PROVIDER_NAME, PROVIDER_TYPE, NPI, UNIQUE_ID, REASON, EFFECTIVE_DATE)
"""The header of the first sheet, the providers currently suspended or excluded."""

REINSTATED_COLUMNS: Final = (
    PROVIDER_NAME,
    PROVIDER_TYPE,
    NPI,
    REASON,
    EFFECTIVE_DATE,
    REINSTATED,
    NOTES,
)
"""The header of the second sheet, the providers reinstated and removed from the list."""

ORGANIZATION_TYPES: Final = frozenset(
    {
        "ADULT FOSTER CARE / GROUP ADULT FOSTER CARE",
        "CERTIFIED INDEPENDENT LABORATORY",
        "CONTINUOUS SKILLED NURSING AGENCY",
        "DURABLE MEDICAL EQUIPMENT",
        "GROUP PRACTICE ORGANIZATION",
        "HOME HEALTH AGENCY",
        "HOSPICE CARE",
        "MENTAL HEALTH CENTER",
        "PHARMACY",
        "PROSTHETICS",
        "REST HOME",
        "SKILLED NURSING AGENCY",
        "SPECIAL PROGRAMS",
        "TRANSPORTATION",
    }
)
"""Provider types (upper case, whitespace collapsed) that MassHealth enrols only as entities;
a name under one of them is an organization unless it carries a personal credential."""

_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
_STATE: Final = "MA"
_ANCHOR: Final = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
_TAG: Final = re.compile(r"<[^>]*>")
_FILE_LINK: Final = re.compile(r"/doc/[^/?#]*suspended[^/?#]*excluded[^/?#]*/download$")
_XLSX_LABEL: Final = re.compile(r"\bXLSX\b", re.IGNORECASE)

LANDING_URL: Final = (
    "https://www.mass.gov/info-details/learn-about-suspended-or-excluded-masshealth-providers"
)
"""The page that links the current edition, as a PDF and as a workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ma_masshealth",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="List of Suspended or Excluded MassHealth Providers",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
)


class MaMassHealthConnector(BulkListConnectorBase):
    """Finds the current workbook on the MassHealth page, downloads it and maps both sheets."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The list's workbook link: a ``/doc/.../download`` link labelled as an XLSX file."""
        for anchor in _ANCHOR.finditer(html):
            if not _XLSX_LABEL.search(unescape(_TAG.sub(" ", anchor[2]))):
                continue
            try:
                return first_href(anchor[1], _FILE_LINK)
            except ParseError:
                continue
        raise ParseError("no XLSX link to the suspended or excluded providers list")

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map the listed and the reinstated providers; rows repeating an identity are merged."""
        rows = chain(
            read_sheet(body, sheet=0, expected_columns=COLUMNS),
            read_sheet(body, sheet=1, expected_columns=REINSTATED_COLUMNS),
        )
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=(PROVIDER_TYPE, REASON))
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


def name_text(cell: str) -> str:
    """The name cell without a comma that only sets off a suffix or a credential ("FIRST M
    LAST, II", "FIRST LAST, MD PC"), so it is read first name first and a professional
    corporation like its comma-less spelling; a comma before anything else is kept."""
    head, comma, tail = cell.rpartition(",")
    words = tail.split()
    if (
        comma
        and head.strip()
        and words
        and holds_only_credentials(words[0])
        and all(holds_only_credentials(word) or _is_designator(word) for word in words)
    ):
        return f"{head.strip()} {tail.strip()}"
    return cell


def _is_designator(word: str) -> bool:
    return word.replace(".", "").upper() in CORPORATE_DESIGNATORS


def is_organization_type(provider_type: str | None) -> bool:
    """Whether the provider type is one MassHealth enrols only as an entity."""
    return (collapse(provider_type) or "").upper() in ORGANIZATION_TYPES


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    text = collapse(row[PROVIDER_NAME])
    if text is None:
        raise ParseError("row has no provider name")
    name = parse_name_field(
        name_text(text),
        organization_hint=is_organization_type(row[PROVIDER_TYPE]),
        first_name_first=True,
    )
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    npis = parse_npi_list(row[NPI])
    reinstated = row.get(REINSTATED)
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=clean(row.get(UNIQUE_ID)),
        action=ExclusionAction.SANCTION,
        effective_date=issues.date(EFFECTIVE_DATE, row[EFFECTIVE_DATE], _DATE_FORMATS),
        end_date=issues.date(REINSTATED, reinstated, _DATE_FORMATS) if reinstated else None,
        reason=clean(row[REASON]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
