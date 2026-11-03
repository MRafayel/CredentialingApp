"""Connector for the New Hampshire DHHS "Provider Exclusion and Sanctions List" (Medicaid).

Source facts (survey of 2026-10-16 to 2026-10-22, re-checked 2026-11-02):

- Landing page: https://www.dhhs.nh.gov/programs-services/medicaid/medicaid-provider-relations
  (Drupal behind Akamai). It links the workbook as "Provider Exclusion and Sanctions List" at
  ``/sites/g/files/ehbemt476/files/documents2/provider-sanctions-list-update.xlsx``. The URL
  stayed the same from the 2026-10-22 survey ("Updated 5/21/26", 76 rows) to the 2026-11-02
  edition ("Updated 10/26/26"), so the file is downloaded directly and the landing page is
  not fetched at all.
- The file answers ``200`` with ``Last-Modified`` and no ETag (``Cache-Control:
  max-age=300``; 17,113 bytes on 2026-11-02): change signal ``CONDITIONAL_GET``
  (``If-Modified-Since``, a ``304`` is unchanged; a ``200`` with the same bytes is unchanged
  too, by content hash). One request per weekly poll.
- In October 2026 Akamai answered scripts and headless Chrome ``403``, robots.txt included,
  while headed Chrome passed; on 2026-11-02 plain HTTP got ``200`` for the landing page and
  the file. ``browser_fallback`` repeats a refused request in headed Chrome, which has no
  conditional request and compares the content hash instead; on 2026-11-02 Chrome received
  the file as a download (no Content-Disposition), byte for byte the plain-HTTP body. The
  survey's robots.txt request was refused too, so no crawl delay is published; the connector
  waits 10 s before every request, by our choice.
- One sheet ("Sheet1"): a title cell ("Updated 10/26/26") on row 1, the header on row 2:
  ``Provider/Individual Last Name | Provider/Individual First Name | Business Name |
  Provider/Individual Type | National Provider Identifier (NPI) | Exclusion/Sanction Reason |
  Exclusion/Sanction Effective Date``, then the data (77 rows) and one empty row.
- Quality notes (2026-11-02): the NPI cell is a number, digits typed as text, "N/A" or "n/a",
  empty, or once an organization's FEIN with "(FEIN)" (kept only in ``raw``); every ten-digit
  value was a valid NPI. The effective date is an Excel date on all rows but one, which holds
  two different dates typed as text: counted unreadable, the date left empty, never guessed.
  The reason is free text ("Medicare Revocation", "Board action Voluntary surrender of
  license", "Medicaid Fraud conviction", "OIG LEIE and SAM Exclusion", some with a date).
  The type includes "RECIPT/BENEFICIARY" rows (beneficiaries, not providers) and one
  "...-owner". No licence, address, date of birth or end date is published.
- Mapping: a row is a person when the last name is set, otherwise the organization in
  ``Business Name``; a row with neither is skipped. A business on a person's row is another
  party (a related party), never an alias. The first word of the first-name cell is the
  first name and the rest the middle name ("AVERY J." gives first "AVERY", middle "J."); a
  generational suffix comes off the last name (``split_suffix``). The list does not state an
  action per row, only the reason: the action is an exclusion when the reason names one
  ("exclu"), otherwise a sanction; the reason is ``action_raw`` and ``reason``. Removal from
  the list is the only reinstatement signal.
- One person appears on five rows, each with its own NPI and some with other dates: five
  identities, five records. Rows repeating one identity would be merged
  (``merge_duplicates``, type and reason joined); none did on 2026-11-02.
- First parse 2026-11-02 (offline, from the downloaded file): 77 rows (the empty row is not
  read), 77 records, none skipped or merged: 64 individuals and 13 organizations, an NPI on
  52, three related businesses, three suffixes, a middle name on 23, one unreadable
  effective date; 21 exclusions and 56 sanctions. The end-to-end fetch the same day was one
  plain-HTTP request, ``COMPLETED`` with the same counts.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field, split_suffix
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
    PersonName,
    SourceSnapshot,
)

LAST_NAME: Final = "Provider/Individual Last Name"
FIRST_NAME: Final = "Provider/Individual First Name"
BUSINESS: Final = "Business Name"
PROVIDER_TYPE: Final = "Provider/Individual Type"
NPI: Final = "National Provider Identifier (NPI)"
REASON: Final = "Exclusion/Sanction Reason"
EFFECTIVE_DATE: Final = "Exclusion/Sanction Effective Date"
COLUMNS: Final = (LAST_NAME, FIRST_NAME, BUSINESS, PROVIDER_TYPE, NPI, REASON, EFFECTIVE_DATE)

ACTION_KEYWORDS: Final = (("exclu", ExclusionAction.EXCLUSION),)
"""Keywords of the reason, first match wins; any other listed row is a ``SANCTION``."""

JOINED: Final = (PROVIDER_TYPE, REASON)
"""Raw columns that keep every distinct value when rows of one identity are merged."""

_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
_STATE: Final = "NH"

DOWNLOAD_URL: Final = (
    "https://www.dhhs.nh.gov/sites/g/files/ehbemt476/files/documents2/"
    "provider-sanctions-list-update.xlsx"
)
"""The file itself, as the connector fetches it."""

SOURCE_URL: Final = "https://www.dhhs.nh.gov/programs-services/medicaid/medicaid-provider-relations"
"""The Medicaid Provider Relations page that links the workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="nh_dhhs",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=SOURCE_URL,
    display_name="New Hampshire Medicaid Provider Exclusion and Sanctions List",
    download_url=DOWNLOAD_URL,
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
)


class NhDhhsConnector(BulkListConnectorBase):
    """Downloads the New Hampshire sanctions workbook and maps each row into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the first sheet, then merge rows that repeat one identity."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=JOINED)
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    last_cell = collapse(row[LAST_NAME])
    business = collapse(row[BUSINESS])
    person: PersonName | None = None
    organization: str | None = None
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    if last_cell is not None:
        entity_kind = EntityKind.INDIVIDUAL
        person = _person(last_cell, collapse(row[FIRST_NAME]))
        related = (business,) if business else ()
    elif business is not None:
        listed = parse_name_field(business, organization_hint=True)
        entity_kind = EntityKind.ORGANIZATION
        organization = listed.organization or business
        aliases = listed.aliases if listed.organization else ()
    else:
        raise ParseError("row has neither a last name nor a business name")
    npis = parse_npi_list(row[NPI])
    reason = clean(row[REASON])
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
        action=classify_action(reason, ACTION_KEYWORDS),
        action_raw=reason,
        effective_date=issues.date(EFFECTIVE_DATE, row[EFFECTIVE_DATE], _DATE_FORMATS),
        reason=reason,
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )


def _person(last_cell: str, first_cell: str | None) -> PersonName:
    """The person from the two name cells: suffix off the last name, middle after the first."""
    last, suffix = split_suffix(last_cell)
    given, *middle = (first_cell or "").split() or [""]
    return PersonName(last=last, first=given, middle=" ".join(middle) or None, suffix=suffix)
