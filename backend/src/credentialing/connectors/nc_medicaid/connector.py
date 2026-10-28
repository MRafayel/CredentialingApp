"""Connector for the North Carolina Medicaid State Excluded Provider List.

Source facts (survey of 2026-10-16, first sync 2026-10-27):

- File: ``https://medicaid.ncdhhs.gov/nc-medicaid-state-excluded-provider-list/download?attachment``,
  a stable alias linked from https://medicaid.ncdhhs.gov/providers/excluded-providers; the
  inner file name and the sheet name carry the month, so the first sheet is read.
- ETag (Apache, with a Dynatrace suffix, compared as an opaque string) and Last-Modified; both
  conditional headers give ``304``: change signal ``CONDITIONAL_GET``. "Updated monthly";
  polled weekly.
- Rows 1-6 are a preamble; the header is on row 7: ``EXCLUDED ENTITY | NPI/ATYPICAL ID
  (EXCLUDED) | EFFECTIVE DATE | REASON FOR EXCLUSION | CITY | STATE | ZIP CODE | OWNERSHIP``.
  About 180 rows.
- ``EXCLUDED ENTITY`` holds people as "FIRST MIDDLE LAST" and organizations in the same column,
  so names are split first name first (the last word is the last name, with any surname
  particles before it). ``NPI/ATYPICAL ID`` holds NPIs, "N/A" and eight-digit atypical
  provider ids, which become ``state_provider_id``. Dates are mostly Excel dates, a few typed
  as text. ``REASON FOR EXCLUSION`` is a code (STATE, OIG, FEDERAL and combinations), kept as
  the reason. Non-breaking spaces occur.
- ``OWNERSHIP`` names the owners of an excluded entity, who are not excluded themselves: it
  stays in ``raw`` only and is never an alias, so it is never matched.
- First sync 2026-10-27: 33,070 bytes (sheet "SEPTEMBER 2026", ETag and Last-Modified sent),
  181 rows, none skipped, 170 distinct records (an entity is repeated once per owner): 102
  individuals and 68 organizations, NPI on 154, an atypical id on 3. Beyond the survey:
  maiden names in parentheses ("FIRST LAST (MAIDEN)"), "(DBA: ...)" aliases, entity names
  without a business word ("... EAR NOSE AND THROAT"), and trailing spaces in the header.
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
    parse_npi_list,
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
    SourceSnapshot,
)

COLUMNS: Final = (
    "EXCLUDED ENTITY",
    "NPI/ATYPICAL ID (EXCLUDED)",
    "EFFECTIVE DATE",
    "REASON FOR EXCLUSION",
    "CITY",
    "STATE",
    "ZIP CODE",
    "OWNERSHIP",
)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
_STATE: Final = "NC"

DOWNLOAD_URL: Final = (
    "https://medicaid.ncdhhs.gov/nc-medicaid-state-excluded-provider-list/download?attachment"
)
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="nc_medicaid",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url="https://medicaid.ncdhhs.gov/providers/excluded-providers",
    display_name="North Carolina Medicaid State Excluded Provider List",
    download_url=DOWNLOAD_URL,
)


class NcMedicaidConnector(BulkListConnectorBase):
    """Downloads the North Carolina workbook and maps each row of its first sheet."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; ``OWNERSHIP`` stays in ``raw`` and is never matched."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name = parse_name_field(row["EXCLUDED ENTITY"], first_name_first=True)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no excluded entity")
    identifier = clean(row["NPI/ATYPICAL ID (EXCLUDED)"])
    npis = parse_npi_list(identifier)
    address = Address(
        city=clean(row["CITY"]),
        state=parse_state(row["STATE"]),
        postal_code=parse_us_zip(row["ZIP CODE"]),
    )
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        npi=npis[0] if npis else None,
        state_provider_id=identifier if identifier and not npis and len(identifier) != 10 else None,
        address=address if address != Address() else None,
        action=ExclusionAction.EXCLUSION,
        effective_date=issues.date("EFFECTIVE DATE", row["EFFECTIVE DATE"], _DATE_FORMATS),
        reason=clean(row["REASON FOR EXCLUSION"]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
