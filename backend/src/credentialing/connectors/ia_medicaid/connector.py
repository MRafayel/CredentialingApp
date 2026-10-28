"""Connector for the Iowa Medicaid list of excluded individuals and entities (HHS).

Source facts (survey of 2026-10-16, first sync 2026-10-27):

- File: ``https://hhs.iowa.gov/media/19531/download?inline`` (a Drupal media URL; the inner
  file name and the sheet name carry the month, so the first sheet is read), linked from
  https://hhs.iowa.gov/medicaid/provider-services/excluded-individuals-and-entities. About
  1,300 rows.
- Last-Modified, no ETag; ``If-Modified-Since`` gives ``304``: change signal
  ``CONDITIONAL_GET``. Monthly in practice; polled weekly.
- Header on row 1: ``Enrollment Type, Specialty, NPI, Affiliated NPI, Last Name, First Name,
  Legal Business Name, Type of Sanction, Effective Date, Sanction End Date, Eligible to
  Reapply Date, Authority, State License Type, State License Number``.
- ``Enrollment Type`` is "Individual" or "Organization" with typos, so only its start is
  compared; when it is unreadable the name columns decide.
- ``NPI`` holds NPIs, Iowa Medicaid ids shaped ``X000000111`` (stored as
  ``state_provider_id``) and numbers that are not ten digits (left in ``raw``).
  ``Affiliated NPI`` (an NPI of an affiliated party, filled on a handful of organizations)
  becomes ``related_npis`` when it is a valid NPI; anything else (a Medicaid id) stays in
  ``raw``.
- ``Type of Sanction`` is classified by keyword and kept as ``action_raw``; ``Authority``
  is the reason. ``Sanction End Date`` mixes dates with "Indefinite" and "Federal Authority",
  which mean no end date. One person can have several rows (a state and a federal action).
- Licences are Iowa licences with the licence type as their kind.
- First sync 2026-10-27: 146,739 bytes (sheet "Sept 2026", Last-Modified only), 1,318 rows,
  none skipped, 1,317 distinct records: 1,061 individuals and 256 organizations, NPI on 410,
  Medicaid id on 347, licence on 470, an end date on 170 (141 already past). Beyond the
  survey: end dates "2 Years" and "On Payment Plan" (no end date) and one effective date
  "12/17/2025 - on payment plan" (unreadable, counted).
- Re-sync 2026-10-28: ``Affiliated NPI`` is filled on 4 organization rows; 3 hold a valid
  NPI (3 records with a related NPI) and one a Medicaid id.
"""

import re
from collections.abc import Mapping
from datetime import date, timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    collapse,
    entity_kind_from,
    open_ended,
    parse_npi,
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

COLUMNS: Final = (
    "Enrollment Type",
    "Specialty",
    "NPI",
    "Affiliated NPI",
    "Last Name",
    "First Name",
    "Legal Business Name",
    "Type of Sanction",
    "Effective Date",
    "Sanction End Date",
    "Eligible to Reapply Date",
    "Authority",
    "State License Type",
    "State License Number",
)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "IA"
_MEDICAID_ID: Final = re.compile(r"^[A-Z][0-9]{6,}$")

ACTION_KEYWORDS: Final = (
    ("exclu", ExclusionAction.EXCLUSION),
    ("suspen", ExclusionAction.SUSPENSION),
    ("terminat", ExclusionAction.TERMINATION),
)
"""Keywords of ``Type of Sanction``, first match wins; anything else is a ``SANCTION``."""

DOWNLOAD_URL: Final = "https://hhs.iowa.gov/media/19531/download?inline"
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ia_medicaid",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url="https://hhs.iowa.gov/medicaid/provider-services/excluded-individuals-and-entities",
    display_name="Iowa Medicaid Excluded Individuals and Entities",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("Specialty",),
)


class IaMedicaidConnector(BulkListConnectorBase):
    """Downloads the Iowa workbook and maps each row of its first sheet."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; the specialty stays in ``raw``, a valid affiliated NPI is related."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def entity_kind_for(
    enrollment_type: str | None, business: str | None, last: str | None
) -> EntityKind:
    """From the enrollment type's start ("Indvidual" counts), else from the name columns."""
    folded = (enrollment_type or "").strip().casefold()
    if folded.startswith("ind"):
        return EntityKind.INDIVIDUAL if last else entity_kind_from(business, last)
    if folded.startswith("org"):
        return EntityKind.ORGANIZATION if business else entity_kind_from(business, last)
    return entity_kind_from(business, last)


def _end_date(text: str | None, issues: RowIssues) -> date | None:
    """A date, or no end date for the words the list writes instead ("Indefinite")."""
    value = clean(text)
    if value is not None and any(char.isalpha() for char in value):
        return None
    return open_ended(issues.date("Sanction End Date", value, _DATE_FORMATS))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    last = collapse(row["Last Name"])
    business = collapse(row["Legal Business Name"])
    entity_kind = entity_kind_for(row["Enrollment Type"], business, last)
    if entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has neither a business nor a last name")
    individual = entity_kind is EntityKind.INDIVIDUAL and last is not None
    identifier = (clean(row["NPI"]) or "").upper()
    license_number = clean(row["State License Number"])
    sanction = clean(row["Type of Sanction"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=entity_kind,
        person_name=PersonName(last=last, first=collapse(row["First Name"]) or "")
        if individual and last
        else None,
        organization_name=None if individual else business,
        aliases=(business,) if individual and business else (),
        npi=parse_npi(identifier),
        related_npis=parse_npi_list(row["Affiliated NPI"]),
        state_provider_id=identifier if _MEDICAID_ID.match(identifier) else None,
        license=License(number=license_number, state=_STATE, kind=clean(row["State License Type"]))
        if license_number
        else None,
        action=classify_action(sanction, ACTION_KEYWORDS),
        action_raw=sanction,
        effective_date=issues.date("Effective Date", row["Effective Date"], _DATE_FORMATS),
        end_date=_end_date(row["Sanction End Date"], issues),
        reason=clean(row["Authority"]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
