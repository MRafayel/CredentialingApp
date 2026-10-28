"""Connector for the Maryland Department of Health (MDH) sanctioned provider list.

Source facts (survey of 2026-10-16, first sync 2026-10-27):

- File: ``https://health.maryland.gov/mmcp/provider/Documents/sanctioned-list/State_of_MD_Sanctioned_Providers.xlsx``
  (SharePoint behind Cloudflare), linked from
  https://health.maryland.gov/mmcp/provider/Pages/sanctioned_list.aspx. About 1,600 rows.
- ETag ``"{GUID},version"`` and Last-Modified; both conditional headers give ``304``: change
  signal ``CONDITIONAL_GET``. "Monthly if needed"; polled weekly. robots.txt disallows the
  SharePoint APIs, which are not used.
- Header on row 1: ``LAST NAME/ORGANIZATION | FIRST NAME | TYPE OF ENTITY/PROFESSION |
  SANCTION TYPE | NPI | LICENSE NO | TERMINATION/SANCTION DATE | ADDRESS | CITY/STATE/ZIP``,
  then 1,615 data rows. The data ends at the first blank row; below it the sheet holds the
  sanction-code legend and the list date ("10.08.2026" on 2026-10-27), which are not
  records. The sheet declares 26 columns and about 120 empty rows after the legend.
- An empty ``FIRST NAME`` marks an organization, named in the first column. NPI on about 17%
  of rows; two cells hold two NPIs (the first is the record's, the rest stay in ``raw``), and
  four hold ten digits that are no valid NPI (left in ``raw``).
- ``SANCTION TYPE`` is a code: HHS, LB, MDH, F, MFVVU, MFVVU-A, OIG, LC, LB/HHS, blank on 7
  rows. The file's own legend (:data:`SANCTION_CODES`) explains A, LB, HHS, MDH, MFVVU and F;
  its meaning becomes the record's ``reason`` (each part of "LB/HHS"); MFVVU-A, OIG and LC
  are not explained. Every code is a ``SANCTION`` with the code as ``action_raw``, except
  that a code naming OIG or HHS is an ``EXCLUSION``. The legend's HHS is broader than a
  federal exclusion ("other state Medicaid program, or Medicare program"); the owner keeps
  that mapping.
- ``CITY/STATE/ZIP`` is one cell ("Baltimore, MD 21201", sometimes without the state), split
  best effort; licences are Maryland licences.
- First sync 2026-10-27: 243,646 bytes, 1,615 rows, none skipped, 1,606 distinct records:
  1,436 individuals and 170 organizations, NPI on 272, licence on 455.
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
from credentialing.connectors.framework.values import (
    clean,
    collapse,
    parse_city_state_zip,
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
    Address,
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)

COLUMNS: Final = (
    "LAST NAME/ORGANIZATION",
    "FIRST NAME",
    "TYPE OF ENTITY/PROFESSION",
    "SANCTION TYPE",
    "NPI",
    "LICENSE NO",
    "TERMINATION/SANCTION DATE",
    "ADDRESS",
    "CITY/STATE/ZIP",
)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "MD"

FEDERAL_EXCLUSION_CODES: Final = frozenset({"OIG", "HHS"})
"""Sanction codes read as an exclusion; any other code is a sanction."""

SANCTION_CODES: Final = {
    "A": "Abuse of adult/vulnerable individual",
    "LB": "Licensing Board discipline (Suspended, surrendered, revoked license)",
    "HHS": (
        "Sanctioned by U.S. Department of Health and Human Services, other state Medicaid "
        "program, or Medicare program"
    ),
    "MDH": "Other sanction by Maryland Department of Health Department",
    "MFVVU": "Medicaid Fraud And Vulnerable Victims Unit conviction",
    "F": "Other Medicaid fraud conviction",
}
"""The legend printed below the data in the file (as observed 2026-10-27), word for word."""

DOWNLOAD_URL: Final = (
    "https://health.maryland.gov/mmcp/provider/Documents/sanctioned-list/"
    "State_of_MD_Sanctioned_Providers.xlsx"
)
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="md_medicaid",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url="https://health.maryland.gov/mmcp/provider/Pages/sanctioned_list.aspx",
    display_name="Maryland Medicaid Sanctioned Provider List",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("TYPE OF ENTITY/PROFESSION",),
)


class MdMedicaidConnector(BulkListConnectorBase):
    """Downloads the Maryland workbook and maps each row into a sanction record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row above the legend; the profession stays in ``raw``."""
        rows = read_sheet(body, expected_columns=COLUMNS, stop_at_blank_row=True)
        return build_records(rows, partial(_record, snapshot))


def _code_parts(code: str | None) -> list[str]:
    return [part.strip().upper() for part in (code or "").split("/") if part.strip()]


def action_for(code: str | None) -> ExclusionAction:
    """``EXCLUSION`` when any part of the code ("LB/HHS") is OIG or HHS, else ``SANCTION``."""
    if set(_code_parts(code)) & FEDERAL_EXCLUSION_CODES:
        return ExclusionAction.EXCLUSION
    return ExclusionAction.SANCTION


def reason_for(code: str | None) -> str | None:
    """The legend's meaning of every part of the code, or ``None`` when a part is unexplained."""
    parts = _code_parts(code)
    if not parts or any(part not in SANCTION_CODES for part in parts):
        return None
    return "; ".join(SANCTION_CODES[part] for part in parts)


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    last = collapse(row["LAST NAME/ORGANIZATION"])
    if last is None:
        raise ParseError("row has no name")
    first = collapse(row["FIRST NAME"])
    entity_kind = EntityKind.INDIVIDUAL if first else EntityKind.ORGANIZATION
    npis = parse_npi_list(row["NPI"])
    license_number = clean(row["LICENSE NO"])
    code = clean(row["SANCTION TYPE"])
    street = clean(row["ADDRESS"])
    place = parse_city_state_zip(row["CITY/STATE/ZIP"]) or Address()
    if street:
        place = (
            place.model_copy(update={"line1": street})
            if place.line1 is None
            else Address(line1=street, line2=place.line1)
        )
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=entity_kind,
        person_name=PersonName(last=last, first=first) if first else None,
        organization_name=None if first else last,
        npi=npis[0] if npis else None,
        license=License(number=license_number, state=_STATE) if license_number else None,
        address=place if place != Address() else None,
        action=action_for(code),
        action_raw=code,
        reason=reason_for(code),
        effective_date=issues.date(
            "TERMINATION/SANCTION DATE", row["TERMINATION/SANCTION DATE"], _DATE_FORMATS
        ),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
