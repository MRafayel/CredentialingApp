"""Connector for the Washington Health Care Authority provider termination and exclusion list.

Source facts (survey of 2026-10-16, first sync 2026-10-27):

- File: ``https://www.hca.wa.gov/assets/billers-and-providers/termination-exclusion.xlsx``,
  linked from https://www.hca.wa.gov/billers-providers-partners/become-apple-health-provider/provider-termination-and-exclusion-list.
  The page's second file (``termination-exclusion-dshs.xlsx``, DSHS social services, one row,
  last edited 2022) is not read. About 230 rows.
- ETag and Last-Modified are sent, but the CDN answers conditional requests with ``200``:
  change signal ``HEADER_COMPARE`` (HEAD, compare, GET on change). Polled weekly.
- Header on row 4: ``Name | License # | NPI # or P1 # | Date of Exclusion | Action``.
- ``Name`` is "Last, First M. CREDENTIAL" (ARNP, PA-C, LD, MW, SUDP, ...) or an
  organization; some cells hold several names on separate lines ("Last, First MD" then
  "dba: Clinic Name", a person then their business, or two business names and then the
  owner). The first line is the party; a "dba" line or one naming the same kind of party is
  an alias, and one naming the other kind (the owner, the business) is a related party
  (``parse_name_lines``).
- ``NPI # or P1 #`` holds NPIs or ProviderOne ids written "P1 - 1234567", whose digits become
  ``state_provider_id``. Some cells hold two NPIs, checked against NPPES (2026-10-28): always
  one person's (type 1) and that person's business's (type 2), in no reliable order, so they
  cannot be paired with the names by position. The first is the record's ``npi``, the others
  ``related_npis``. Licences are Washington licences; a second licence stays in ``raw``.
- ``Action`` is free text with typos that mixes licence actions, OIG exclusions and
  terminations; it is classified by keyword (see :data:`ACTION_KEYWORDS`) and kept as
  ``action_raw``.
- First sync 2026-10-27: 27,792 bytes (HEAD, then GET), 234 rows, none skipped, 234 records:
  226 individuals and 8 organizations, NPI on 180, ProviderOne id on 32, licence on 232.
  Some ten-digit values (``52037...00``) are no valid NPI and stay in ``raw``.
- Re-sync 2026-10-28 (related NPIs and parties): 12 identifier cells hold several values:
  10 two NPIs (10 records with a related NPI), one an NPI and a ProviderOne id, one three
  ProviderOne ids (the first is kept, the others stay in ``raw``). 9 name cells hold several
  lines: 8 records with a related party, 3 with an alias. 13 records changed, 221 unchanged.
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_lines
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    parse_npi_list,
    split_identifiers,
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
    SourceSnapshot,
)

COLUMNS: Final = ("Name", "License #", "NPI # or P1 #", "Date of Exclusion", "Action")
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_STATE: Final = "WA"
_PROVIDER_ONE: Final = re.compile(r"P\s*1\s*[-#:]?\s*([0-9]{5,})", re.IGNORECASE)

ACTION_KEYWORDS: Final = (
    ("exclu", ExclusionAction.EXCLUSION),
    ("terminat", ExclusionAction.TERMINATION),
    ("suspen", ExclusionAction.SUSPENSION),
    ("revo", ExclusionAction.SANCTION),
    ("expir", ExclusionAction.SANCTION),
    ("licen", ExclusionAction.SANCTION),
)
"""Keywords of the ``Action`` text, first match wins; anything else is a ``SANCTION``."""

DOWNLOAD_URL: Final = (
    "https://www.hca.wa.gov/assets/billers-and-providers/termination-exclusion.xlsx"
)
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="wa_hca",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.HEADER_COMPARE,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=(
        "https://www.hca.wa.gov/billers-providers-partners/become-apple-health-provider/"
        "provider-termination-and-exclusion-list"
    ),
    display_name="Washington HCA Provider Termination and Exclusion List",
    download_url=DOWNLOAD_URL,
)


class WaHcaConnector(BulkListConnectorBase):
    """Downloads the HCA workbook and maps each row into a termination or exclusion record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; the action is classified by keyword and kept as written."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def provider_one_id(text: str | None) -> str | None:
    """The digits of the first ProviderOne id ("P1 - 1234567") in a cell, if any."""
    found = _PROVIDER_ONE.search(text or "")
    return found[1] if found else None


def _without_comma(line: str) -> bool:
    """People are written "Last, First"; a line without a comma names a business."""
    return "," not in line


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name = parse_name_lines(row["Name"].splitlines(), organization_hint=_without_comma)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no name")
    identifiers = row["NPI # or P1 #"]
    npis = parse_npi_list(identifiers)
    licenses = split_identifiers(clean(row["License #"]))
    action_text = clean(row["Action"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        related_parties=name.related_parties,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=provider_one_id(identifiers),
        license=License(number=licenses[0], state=_STATE) if licenses else None,
        action=classify_action(action_text, ACTION_KEYWORDS),
        action_raw=action_text,
        effective_date=issues.date("Date of Exclusion", row["Date of Exclusion"], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
