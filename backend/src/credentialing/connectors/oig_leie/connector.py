"""Connector for the HHS-OIG List of Excluded Individuals/Entities (LEIE).

Source facts, observed 2026-10-25:

- File: ``https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv``, linked as "Updated LEIE
  Database (CSV)" from https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/
  (the old ``exclusions_list.asp`` redirects there). robots.txt is a 404: no restrictions.
- ``200``, ``text/csv``, ``Content-Disposition: attachment; filename="UPDATED.csv"``,
  10,802,977 bytes, 84,093 data rows, ASCII, CRLF, no BOM, every row 18 fields.
- ``Last-Modified: Fri, 09 Oct 2026 12:51:03 GMT``, no ETag. A request with that
  ``If-Modified-Since`` returns ``304``: change signal ``CONDITIONAL_GET``, one request per
  poll while the file is unchanged.
- Cadence: monthly. The page says each file "include[s] all actions taken during the prior
  month"; reinstated parties are removed from the full file, so a disappearing row is the
  reinstatement signal.
- Columns: ``LASTNAME, FIRSTNAME, MIDNAME, BUSNAME, GENERAL, SPECIALTY, UPIN, NPI, DOB,
  ADDRESS, CITY, STATE, ZIP, EXCLTYPE, EXCLDATE, REINDATE, WAIVERDATE, WVRSTATE``. Dates are
  ``YYYYMMDD``; ``REINDATE`` and ``WAIVERDATE`` are ``0`` when absent (REINDATE is ``0`` on
  every row). ``NPI`` is ``0`` when absent (8,924 rows carry one, all check-digit valid).
  ``DOB`` is empty on 4,327 rows. ``ZIP`` lost its leading zeros (``2134``). Either
  ``BUSNAME`` or ``LASTNAME`` is set, never both. ``EXCLTYPE`` is the statutory authority,
  such as ``1128a1``.
"""

from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.delimited import read_delimited
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.values import (
    clean,
    collapse,
    entity_kind_from,
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
    PersonName,
    SourceSnapshot,
)
from credentialing.domain.types import FEDERAL

COLUMNS: Final = (
    "LASTNAME",
    "FIRSTNAME",
    "MIDNAME",
    "BUSNAME",
    "GENERAL",
    "SPECIALTY",
    "UPIN",
    "NPI",
    "DOB",
    "ADDRESS",
    "CITY",
    "STATE",
    "ZIP",
    "EXCLTYPE",
    "EXCLDATE",
    "REINDATE",
    "WAIVERDATE",
    "WVRSTATE",
)
_DATE_FORMATS: Final = ("%Y%m%d",)

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="oig_leie",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({FEDERAL}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url="https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/",
    display_name="HHS-OIG List of Excluded Individuals/Entities",
)


class OigLeieConnector(BulkListConnectorBase):
    """Downloads the full LEIE file and maps each row into an exclusion record."""

    download_url = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; ``GENERAL``, ``SPECIALTY``, ``UPIN`` and waivers stay in ``raw``."""
        rows = read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    business = collapse(row["BUSNAME"])
    last = collapse(row["LASTNAME"])
    entity_kind = entity_kind_from(business, last)
    if entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has neither a business nor a last name")
    person = (
        PersonName(
            last=last,
            first=collapse(row["FIRSTNAME"]) or "",
            middle=collapse(row["MIDNAME"]),
        )
        if last is not None and entity_kind is EntityKind.INDIVIDUAL
        else None
    )
    address = Address(
        line1=clean(row["ADDRESS"]),
        city=clean(row["CITY"]),
        state=parse_state(row["STATE"]),
        postal_code=parse_us_zip(row["ZIP"]),
    )
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=entity_kind,
        person_name=person,
        organization_name=business,
        npi=parse_npi(row["NPI"]),
        date_of_birth=issues.date("DOB", row["DOB"], _DATE_FORMATS),
        address=address if address != Address() else None,
        action=ExclusionAction.EXCLUSION,
        action_raw=clean(row["EXCLTYPE"]),
        effective_date=issues.date("EXCLDATE", row["EXCLDATE"], _DATE_FORMATS),
        end_date=issues.date("REINDATE", row["REINDATE"], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
