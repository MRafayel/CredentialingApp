"""Connector for the Vermont Medicaid excluded provider list (DVHA, Gainwell portal).

Source facts (survey of 2026-10-19, first sync 2026-10-27):

- File: ``https://www.vtmedicaid.com/assets/provEnroll/excludedProvidersList/ExcludedProvidersList.xlsx``
  (stable URL, located by the owner behind the AngularJS page the state links from
  https://dvha.vermont.gov/providers/medicaid-excluded-providers). No robots.txt on the host.
- Weak ETag and Last-Modified; a conditional GET gives ``304``: change signal
  ``CONDITIONAL_GET``. Monthly by its title; polled weekly. About 70 rows.
- Row 1 is a title ("Vermont Monthly List of For Cause Exclusions - <month>", with a logo
  that reads as ``#VALUE!``), the header is on row 2, the data runs until the first blank row,
  and footnotes and a key follow it.
- The header names ``State`` twice: the first is the provider's state, the second
  (``State (2)`` here) is really a reason code: 07 Medicare or other-state exclusion,
  22 sanctions, 19 contract terms, 08 fraud conviction. It is kept as ``action_raw`` and the
  reason text as ``reason``.
- ``Provider Full Name`` is "LAST,FIRST M" with no space after the comma and heavy trailing
  padding; organizations have no comma. ``VT Medicaid Provider ID`` becomes
  ``state_provider_id``. ``Provider NPI`` holds one NPI per row so far; should a cell hold
  several, the first is the record's ``npi`` and the others ``related_npis``.
- ``Provider Exclusion Status End Date`` is the placeholder 2382-12-31 for open-ended
  exclusions, read as no end date; a ``Reinstatement Date with Vermont Medicaid if
  Applicable`` (empty on every row so far) ends the record. The ID keeps its leading zeros
  (stored as text) and names carry credentials ("LAST,FIRST M DMD").
- First sync 2026-10-27: 65,134 bytes, 69 rows, none skipped, 68 distinct records: 56
  individuals and 12 organizations, NPI on 52. The last header is "Reinstatement Date with
  Vermont Medicaid if Applicable"; counsellor and social-worker credentials (LCMHC, LICSW)
  follow the names.
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
    open_ended,
    parse_npi_list,
    parse_state,
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
    "VT Medicaid Provider ID",
    "Provider NPI",
    "Provider Full Name",
    "Provider Type",
    "Provider City",
    "State",
    "State (2)",
    'Reason or "Cause" for Exclusion',
    "Provider Exclusion Status Effective Date",
    "Provider Exclusion Status End Date",
    "Reinstatement Date with Vermont Medicaid if Applicable",
)
REINSTATED: Final = COLUMNS[-1]
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "VT"

DOWNLOAD_URL: Final = (
    "https://www.vtmedicaid.com/assets/provEnroll/excludedProvidersList/ExcludedProvidersList.xlsx"
)
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="vt_medicaid",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url="https://dvha.vermont.gov/providers/medicaid-excluded-providers",
    display_name="Vermont Medicaid Excluded Provider List",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("Provider Type",),
)


class VtMedicaidConnector(BulkListConnectorBase):
    """Downloads the Vermont workbook and maps the table above its footnotes."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row up to the first blank row; the reason code becomes ``action_raw``."""
        rows = read_sheet(body, expected_columns=COLUMNS, stop_at_blank_row=True)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    text = row["Provider Full Name"]
    name = parse_name_field(text, organization_hint="," not in text)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    npis = parse_npi_list(row["Provider NPI"])
    address = Address(city=clean(row["Provider City"]), state=parse_state(row["State"]))
    end_date = open_ended(
        issues.date(
            "Provider Exclusion Status End Date",
            row["Provider Exclusion Status End Date"],
            _DATE_FORMATS,
        )
    )
    reinstated = issues.date(REINSTATED, row[REINSTATED], _DATE_FORMATS)
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
        state_provider_id=clean(row["VT Medicaid Provider ID"]),
        address=address if address != Address() else None,
        action=ExclusionAction.EXCLUSION,
        action_raw=clean(row["State (2)"]),
        effective_date=issues.date(
            "Provider Exclusion Status Effective Date",
            row["Provider Exclusion Status Effective Date"],
            _DATE_FORMATS,
        ),
        end_date=reinstated or end_date,
        reason=clean(row['Reason or "Cause" for Exclusion']),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
