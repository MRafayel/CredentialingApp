"""Connector for the New York OMIG Medicaid exclusion list.

Source facts (survey of 2026-10-16/19): ``https://apps.omig.ny.gov/exclusions/tabdelimited.aspx``
serves ``ExclusionsAll.txt`` as ``text/plain`` (about 537 KB): ASCII, CRLF, a header
``PROVIDER_NAME, LICENSE_NUMBER, NPI_Number, PROVIDER_TYPE, EXCLUSION_EFFECTIVE_DATE`` and
exactly four tabs per row. Dates are ``MM/DD/YYYY``. There are no cache validators
(``Cache-Control: private``; conditional requests get 200), so the change signal is the
content hash. Refreshed nightly; polled daily.

Individuals and organizations share ``PROVIDER_NAME`` ("LAST FIRST M  MD", AKA/DBA aliases on
about 420 rows); it is split by the framework's name parser, with ``PROVIDER_TYPE`` as a hint
for organizations. No DOB, address, reason or end date is published.
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.delimited import read_delimited
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.values import clean, parse_npi
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
    SourceSnapshot,
)

COLUMNS: Final = (
    "PROVIDER_NAME",
    "LICENSE_NUMBER",
    "NPI_Number",
    "PROVIDER_TYPE",
    "EXCLUSION_EFFECTIVE_DATE",
)
_DATE_FORMATS: Final = ("%m/%d/%Y",)
_STATE: Final = "NY"
_WORDS: Final = re.compile(r"[A-Z]+")

ENTITY_TYPE_NOUNS: Final = frozenset(
    {
        "PHARMACY", "CLINIC", "HOSPITAL", "LABORATORY", "AGENCY", "FACILITY", "CORPORATION",
        "COMPANY", "CENTER", "PRACTICE", "GROUP", "BUSINESS", "SERVICES", "SERVICE",
        "HOSPICE", "HOME", "DISTRIBUTOR", "SCHOOL",
    }
)  # fmt: skip
"""Nouns that end an entity's provider type ("Physical Therapy Practice", "Sober Home").
Only the type's last word counts: "Pharmacy Technician" names a person. "Care" is left out:
it ends person types too ("Nurse Practitioner-Acute Care")."""

PERSON_ROLE_WORDS: Final = frozenset(
    {
        "EMPLOYEE", "EMPLOYEER", "TECHNICIAN", "TECHNOLOGIST", "CLERK", "MANAGER", "OWNER",
        "ADMINISTRATOR", "DIRECTOR", "PRESIDENT", "OFFICER", "ASSISTANT", "CONSULTANT", "AIDE",
        "EXECUTIVE", "SUPERVISOR", "DENTIST", "PHYSICIAN",
    }
)  # fmt: skip
"""Words that make a provider type a person's role even when it ends in an entity noun
("Owner-Pharmacy", "Physician Practice"): then the name alone decides."""

DOWNLOAD_URL: Final = "https://apps.omig.ny.gov/exclusions/tabdelimited.aspx"
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ny_omig",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=1),
    source_url="https://omig.ny.gov/medicaid-fraud/medicaid-exclusions",
    display_name="New York OMIG Medicaid Exclusion List",
    download_url=DOWNLOAD_URL,
    provider_type_fields=("PROVIDER_TYPE",),
)


class NyOmigConnector(BulkListConnectorBase):
    """Downloads the OMIG tab-delimited export and maps each row into an exclusion record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; the provider type stays in ``raw`` and hints at organizations."""
        rows = read_delimited(
            body, delimiter="\t", encoding="utf-8", expected_columns=COLUMNS, quoted=False
        )
        return build_records(rows, partial(_record, snapshot))


def is_entity_type(provider_type: str) -> bool:
    """Whether the provider type names an entity: it ends in an entity noun and has no role.

    A person filed as an organization can never match by name, so the hint is narrow: it fires
    only on types such as "Dental Practice" or "Home Care Agency", never on "Clinic Employee"
    or "Owner-Pharmacy".
    """
    words = _WORDS.findall(provider_type.upper())
    return bool(words) and words[-1] in ENTITY_TYPE_NOUNS and PERSON_ROLE_WORDS.isdisjoint(words)


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name = parse_name_field(
        row["PROVIDER_NAME"], organization_hint=is_entity_type(row["PROVIDER_TYPE"])
    )
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    license_number = clean(row["LICENSE_NUMBER"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        npi=parse_npi(row["NPI_Number"]),
        license=License(number=license_number, state=_STATE) if license_number else None,
        action=ExclusionAction.EXCLUSION,
        effective_date=issues.date(
            "EXCLUSION_EFFECTIVE_DATE", row["EXCLUSION_EFFECTIVE_DATE"], _DATE_FORMATS
        ),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
