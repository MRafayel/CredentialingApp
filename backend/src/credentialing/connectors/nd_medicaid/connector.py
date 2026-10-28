"""Connector for the North Dakota Medicaid provider exclusion list (HHS).

Source facts (survey of 2026-10-19, first sync 2026-10-27):

- File: ``https://www.hhs.nd.gov/sites/default/files/documents/provider-exclusion-list.xlsx``
  (the link text carries the month), linked from
  https://www.hhs.nd.gov/healthcare/medicaid/provider/compliance/fraud-and-abuse. About 190
  rows.
- Strong ETag and Last-Modified; a conditional GET gives ``304``: change signal
  ``CONDITIONAL_GET``. Monthly by its label; polled weekly.
- Sheet ``ND Provider Exclusion List``: a merged title row with the list date, then the header
  on row 2 (an Excel table): ``Provider Name | Street Address | City | State | Zip | NPI |
  License Number | Provider Type | Exclusion Date | Reason for Exclusion``. Real Excel dates.
- Names are "LAST, FIRST" for people; organizations have no comma. NPI on about a quarter of
  the rows, ``N/A`` on the rest; one nine-digit value is no NPI and stays in ``raw``. ZIP
  codes mix numbers, text and ZIP+4. The full street address is published on every row and
  stored (owner decision of 2026-10-22). Licences are North Dakota licences.
- More than half the rows are Qualified Service Providers, mostly without NPI or licence: a
  match on them rests on name and address and goes to a person.
- First sync 2026-10-27: 45,293 bytes, 192 rows, none skipped, 192 records: 177 individuals
  and 15 organizations, NPI on 49, licence on 54. Beyond the survey: former names written
  "LAST (aka OTHER), FIRST" or "LAST (OTHER, OTHER), FIRST" (read as aliases "OTHER, FIRST")
  and nicknames ("Melinda (Missy)").
"""

from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
    clean,
    parse_npi,
    parse_state,
    parse_us_zip,
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
    SourceSnapshot,
)

COLUMNS: Final = (
    "Provider Name",
    "Street Address",
    "City",
    "State",
    "Zip",
    "NPI",
    "License Number",
    "Provider Type",
    "Exclusion Date",
    "Reason for Exclusion",
)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "ND"

DOWNLOAD_URL: Final = (
    "https://www.hhs.nd.gov/sites/default/files/documents/provider-exclusion-list.xlsx"
)
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="nd_medicaid",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url="https://www.hhs.nd.gov/healthcare/medicaid/provider/compliance/fraud-and-abuse",
    display_name="North Dakota Medicaid Provider Exclusion List",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("Provider Type",),
)


class NdMedicaidConnector(BulkListConnectorBase):
    """Downloads the North Dakota workbook and maps each row into an exclusion record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; the provider type stays in ``raw``."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    text = row["Provider Name"]
    name = parse_name_field(text, organization_hint="," not in text)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    license_number = clean(row["License Number"])
    address = Address(
        line1=clean(row["Street Address"]),
        city=clean(row["City"]),
        state=parse_state(row["State"]),
        postal_code=parse_us_zip(row["Zip"]),
    )
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        npi=parse_npi(row["NPI"]),
        license=License(number=license_number, state=_STATE) if license_number else None,
        address=address if address != Address() else None,
        action=ExclusionAction.EXCLUSION,
        effective_date=issues.date("Exclusion Date", row["Exclusion Date"], _DATE_FORMATS),
        reason=clean(row["Reason for Exclusion"]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
