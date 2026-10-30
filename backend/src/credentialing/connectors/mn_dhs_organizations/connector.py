"""Connector for the Minnesota DHS (MHCP) list of excluded organizations.

Source facts (survey of 2026-10-16 and 2026-10-19, re-checked 2026-10-30):

- File: ``https://www.dhs.state.mn.us/main/idcplg?IdcService=GET_FILE&RevisionSelectionMethod=
  LatestReleased&Rendition=Primary&allowInterrupt=1&dDocName=DHS16_177447``, an xlsx of about
  38 KB (433 rows in October 2026), on the same MHCP page and server as the individuals file
  (``mn_dhs_individuals``, a source of its own so each file has its own snapshot).
- Same server behaviour: no ETag or Last-Modified (``no-store``), so ``CONTENT_HASH`` weekly;
  robots.txt ``Crawl-delay: 5``, declared as ``crawl_delay``; a Radware bot manager that
  redirected every request to ``validate.perfdrive.com`` on 2026-10-16 and 2026-10-30 (let
  through on 2026-10-19), which the fetcher reports as ``BLOCKED``, never as an empty list.
- Columns: ``Provider type description | Practice name | Effective date of exclusion (the survey
  wrote "Effective date"; corrected from the real file on 2026-10-30) | Address line1 |
  Address line2 | City | State | Zip code | Exclusion status``; the status in upper case, an
  address on every row. Every row is an organization and an exclusion, with the status as
  ``action_raw`` and reason. No NPI, licence or end date.
"""

from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import clean, collapse, parse_address
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord, SourceSnapshot

COLUMNS: Final = (
    "Provider type description",
    "Practice name",
    "Effective date of exclusion",
    "Address line1",
    "Address line2",
    "City",
    "State",
    "Zip code",
    "Exclusion status",
)
_DATE_FORMATS: Final = ("%m/%d/%Y", "%Y-%m-%d")
_STATE: Final = "MN"

DOWNLOAD_URL: Final = (
    "https://www.dhs.state.mn.us/main/idcplg?IdcService=GET_FILE"
    "&RevisionSelectionMethod=LatestReleased&Rendition=Primary&allowInterrupt=1"
    "&dDocName=DHS16_177447"
)
"""The file itself, as the connector fetches it."""

SOURCE_URL: Final = (
    "https://www.dhs.state.mn.us/main/idcplg?IdcService=GET_DYNAMIC_CONVERSION"
    "&RevisionSelectionMethod=LatestReleased&dDocName=DHS-295094"
)
"""The MHCP page that links both Minnesota files."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="mn_dhs_organizations",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=SOURCE_URL,
    display_name="Minnesota DHS Excluded Organizations",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("Provider type description",),
    crawl_delay=timedelta(seconds=5),
)


class MnDhsOrganizationsConnector(BulkListConnectorBase):
    """Downloads the Minnesota organizations workbook and maps each row into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the first sheet; every row is an organization."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name = collapse(row["Practice name"])
    if name is None:
        raise ParseError("row has no practice name")
    status = clean(row["Exclusion status"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=EntityKind.ORGANIZATION,
        organization_name=name,
        address=parse_address(
            line1=row["Address line1"],
            line2=row["Address line2"],
            city=row["City"],
            state=row["State"],
            postal_code=row["Zip code"],
        ),
        action=ExclusionAction.EXCLUSION,
        action_raw=status,
        effective_date=issues.date(
            "Effective date of exclusion", row["Effective date of exclusion"], _DATE_FORMATS
        ),
        reason=status,
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
