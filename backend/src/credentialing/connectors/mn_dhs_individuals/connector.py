"""Connector for the Minnesota DHS (MHCP) list of excluded individual providers.

Source facts (survey of 2026-10-16 and 2026-10-19, re-checked 2026-10-30):

- File: ``https://www.dhs.state.mn.us/main/idcplg?IdcService=GET_FILE&RevisionSelectionMethod=
  LatestReleased&Rendition=Primary&allowInterrupt=1&dDocName=DHS16_177448``, an xlsx of about
  64 KB (1,098 rows in October 2026), linked from the MHCP excluded-provider page (Oracle
  WebCenter). ``LatestReleased`` keeps the URL stable; the dated file name is only in
  Content-Disposition ("Excluded Ind Providers 10-01-26.xlsx"). Monthly. Organizations are a
  separate file and source (``mn_dhs_organizations``).
- ``Cache-Control: no-cache, no-store`` and neither ETag nor Last-Modified: change signal
  ``CONTENT_HASH``, a plain GET every week compared by hash.
- robots.txt asks ``Crawl-delay: 5`` and disallows only ``IdcService=DOC_INFO*``; the
  connector declares the 5 s ``crawl_delay`` and the owner wants this source polled slowly.
- A Radware bot manager guards the site. On 2026-10-16 and again on 2026-10-30 every file
  request got ``302`` to ``validate.perfdrive.com`` (an HTML page); on 2026-10-19 it got
  ``200``. The fetcher reports such a redirect as ``BLOCKED``, never as an empty list.
- Columns: ``Provider type Description | Last name | First name | Middle name | Effective date
  of exclusion | Address line1 | Address line2 | City | State | Zip code | Exclusion status``.
  Dates typed as text, M/D/YYYY. 1,041 rows are Direct Support Workers; an address on 57.
  The status ("Medicaid authority", "Medicare Medicaid exclusion", "Denied not eligible") is
  the only reason given: every row is an exclusion, with the status as ``action_raw`` and
  reason. No NPI, licence, DOB or end date.
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
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    PersonName,
    SourceSnapshot,
)

COLUMNS: Final = (
    "Provider type Description",
    "Last name",
    "First name",
    "Middle name",
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
    "&dDocName=DHS16_177448"
)
"""The file itself, as the connector fetches it."""

SOURCE_URL: Final = (
    "https://www.dhs.state.mn.us/main/idcplg?IdcService=GET_DYNAMIC_CONVERSION"
    "&RevisionSelectionMethod=LatestReleased&dDocName=DHS-295094"
)
"""The MHCP page that links both Minnesota files."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="mn_dhs_individuals",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=SOURCE_URL,
    display_name="Minnesota DHS Excluded Individual Providers",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("Provider type Description",),
    crawl_delay=timedelta(seconds=5),
)


class MnDhsIndividualsConnector(BulkListConnectorBase):
    """Downloads the Minnesota individuals workbook and maps each row into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the first sheet; every row is a person."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    last = collapse(row["Last name"])
    if last is None:
        raise ParseError("row has no last name")
    status = clean(row["Exclusion status"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(
            last=last,
            first=collapse(row["First name"]) or "",
            middle=collapse(row["Middle name"]),
        ),
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
