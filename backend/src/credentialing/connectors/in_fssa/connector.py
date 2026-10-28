"""Connector for the Indiana FSSA list of Medicaid and CHIP provider terminations (OMPP).

The state calls this list "not an active or ongoing exclusionary list": it records providers
whose participation was terminated, and reinstatements appear only in a separate PDF. The
owner keeps it in scope as termination history (decision of 2026-10-22), so every row is a
``TERMINATION`` and the display name says "terminations", not "exclusions".

Source facts (survey of 2026-10-16, first sync 2026-10-27):

- File: ``https://www.in.gov/fssa/ompp/files/OMPP_Terminations.xlsx`` (stable), linked from
  https://www.in.gov/fssa/ompp/provider-information/termination-of-provider-participation-in-medicaid-and-chip.
  About 110 rows.
- A weak ETag; ``If-None-Match`` gives ``304`` and ``If-Modified-Since`` is ignored. The
  fetcher sends both, which the server accepts: change signal ``CONDITIONAL_GET``. Polled
  weekly.
- Header on row 2: ``Provider Name | National Provider Identification (NPI) | Service Location
  Address | Termination Effective Date `` (with a trailing space).
- People are written first name first, with the credential after a comma ("Avery L Quinlan",
  "Rowan Castellane, DDS", "Devin Marlow, Podiatrist", "Sasha Okonkwo M.D."): the text after the
  comma becomes the suffix and the rest is split first name first. A one-word name
  ("Centrad") is a business. One cell holds two NPIs on separate lines: the first is the
  record's ``npi``, the other a related NPI (``related_npis``).
- ``Service Location Address`` is "City, ST"; a provider with several practice locations has
  them all in the cell, on separate lines or after runs of spaces. The first is stored as the
  address and the rest stay in ``raw``.
- First sync 2026-10-27: 27,495 bytes (sheet "Sheet1", title "Provider Terminations (As of
  July 2026)"), 112 rows, none skipped, 112 records: 76 individuals and 36 organizations,
  NPI on 105. Re-sync 2026-10-28: 1 record with a related NPI (an organization's).
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    ParsedName,
    looks_like_business,
    parse_name_field,
)
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
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
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord, SourceSnapshot

COLUMNS: Final = (
    "Provider Name",
    "National Provider Identification (NPI)",
    "Service Location Address",
    "Termination Effective Date",
)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "IN"
_LOCATION_SEPARATOR: Final = re.compile(r"\n|\s{3,}")

DOWNLOAD_URL: Final = "https://www.in.gov/fssa/ompp/files/OMPP_Terminations.xlsx"
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="in_fssa",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=(
        "https://www.in.gov/fssa/ompp/provider-information/"
        "termination-of-provider-participation-in-medicaid-and-chip"
    ),
    display_name="Indiana Medicaid Provider Terminations",
    download_url=DOWNLOAD_URL,
)


class InFssaConnector(BulkListConnectorBase):
    """Downloads the Indiana terminations workbook and maps each row."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row into a termination record."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def parse_provider_name(text: str | None) -> ParsedName:
    """A first-name-first name with its credential after a comma, or a business name."""
    collapsed = collapse(text) or ""
    if looks_like_business(collapsed):
        return parse_name_field(collapsed)
    words, _, credential = collapsed.partition(",")
    parsed = parse_name_field(
        words, organization_hint=len(words.split()) == 1, first_name_first=True
    )
    suffix = collapse(credential)
    if parsed.person is None or suffix is None:
        return parsed
    own = parsed.person.suffix
    person = parsed.person.model_copy(update={"suffix": f"{own} {suffix}" if own else suffix})
    return ParsedName(person=person, organization=None, aliases=parsed.aliases)


def first_location(text: str | None) -> str | None:
    """The first of the practice locations listed in one cell."""
    return next((part for part in _LOCATION_SEPARATOR.split(text or "") if part.strip()), None)


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name = parse_provider_name(row["Provider Name"])
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    npis = parse_npi_list(row["National Provider Identification (NPI)"])
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
        address=parse_city_state_zip(first_location(row["Service Location Address"])),
        action=ExclusionAction.TERMINATION,
        effective_date=issues.date(
            "Termination Effective Date", row["Termination Effective Date"], _DATE_FORMATS
        ),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
