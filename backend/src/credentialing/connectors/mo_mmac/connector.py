"""Connector for the Missouri Medicaid Audit and Compliance (MMAC) provider sanction list.

The list records Medicaid provider terminations since 2/3/2011 ("reviewed monthly"; the page
warns that "not all providers ... are on the list"), so every row is a ``TERMINATION``.

Source facts (survey of 2026-10-16, re-checked and first sync 2026-10-30):

- Landing page: https://mmac.mo.gov/providers/provider-sanctions/ (WordPress; robots.txt
  disallows only ``/wp-admin/`` and WPForms uploads, no crawl delay). It links the workbook
  as "View List of Terminations" at ``/wp-content/uploads/sanction-list-YYYY-MM-DD.xlsx``;
  the file name changes with every release, so change signal ``LINK_DISCOVERY``: one GET
  of the landing page per poll, and a download only when the link changes. Polled weekly.
- The file has an ETag (Dynatrace suffix) that gives ``304`` on a given URL;
  ``If-Modified-Since`` alone gives ``200``. On 2026-10-30: ``sanction-list-2026-09-30.xlsx``,
  32,903 bytes, Last-Modified 2026-10-08.
- One sheet, "CURRENT": a title row, then the header ``PROVIDER NAME, NPI, TERM DATE, LETTER
  DATE, PROV TYPE/SPECIALTY, LICENSE #, TERMINATION REASON``; 280 rows.
- People are "LAST, FIRST M" (with "JR" and similar suffixes); organizations have no comma
  unless before a designator ("..., LLC"). Four comma-less names end in a credential
  ("FIRST M LAST MD") and are read as that person, first name first.
- ``NPI`` is a number on most rows, text on some, "N/A" on one. ``TERM DATE`` is an Excel date
  on 266 rows and text ``MM/DD/YY`` on 14. ``LETTER DATE`` (the notice) stays in ``raw``.
- One row per provider type: a provider terminated under several Medicaid provider types
  ("26-CDS", "27-REASSESS") has a row for each, identical otherwise. Such rows are merged
  into one record whose ``PROV TYPE/SPECIALTY`` lists the types, separated by "; ", in file
  order; rows that differ in anything else (a licence per type) stay separate records.
- ``LICENSE #`` is "N/A" on most rows; a number may carry another state's prefix ("KS 0417719",
  "IL-036112430"), otherwise it is a Missouri licence. Only the first of several is kept.
- ``TERMINATION REASON`` is free text (BAD DEBT, FRAUD, OIG EXCLUSION, ...), the record's
  reason.
- First sync 2026-10-30: 280 rows, none skipped, 268 records (12 rows merged into the record
  of the same provider under another type): 104 individuals and 164 organizations, NPI on
  266, licence on 78 (Missouri 69, Kansas 6, Illinois 2, Kentucky 1), one alias. A second
  poll was one request ("link unchanged").
"""

import dataclasses
import re
from collections.abc import Iterable, Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
    clean,
    collapse,
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

PROVIDER_TYPE: Final = "PROV TYPE/SPECIALTY"
COLUMNS: Final = (
    "PROVIDER NAME",
    "NPI",
    "TERM DATE",
    "LETTER DATE",
    PROVIDER_TYPE,
    "LICENSE #",
    "TERMINATION REASON",
)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y")
_STATE: Final = "MO"
_LICENSE_STATES: Final = frozenset({"AR", "IA", "IL", "KS", "KY", "MO", "NE", "OK", "TN"})
"""State prefixes recognized on a licence number: Missouri and its neighbours, the only
ones observed ("KS 0417719", "IL-036112430", "MO R6712")."""
_LICENSE_PREFIX: Final = re.compile(r"^([A-Z]{2})[\s-]+(\S.*)$")
_FILE_LINK: Final = re.compile(r"/wp-content/uploads/(?:[^/]+/)*sanction-list[^/]*\.xlsx$")

LANDING_URL: Final = "https://mmac.mo.gov/providers/provider-sanctions/"
"""The page that links the current workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="mo_mmac",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Missouri Medicaid Provider Terminations",
    provider_type_fields=(PROVIDER_TYPE,),
)


class MoMmacConnector(BulkListConnectorBase):
    """Finds the current workbook on the MMAC page, downloads it and maps each provider."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "View List of Terminations" workbook link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Merge the per-provider-type rows, then map each provider into a record."""
        rows = list(read_sheet(body, expected_columns=COLUMNS))
        parsed = build_records(merge_provider_types(rows), partial(_record, snapshot))
        return dataclasses.replace(parsed, rows=len(rows))


def merge_provider_types(rows: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """One row per provider: rows equal in every column but the provider type are merged,
    their types joined with "; " in file order (repeats dropped)."""
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    types: dict[tuple[str, ...], list[str]] = {}
    for row in rows:
        key = tuple(collapse(row[column]) or "" for column in COLUMNS if column != PROVIDER_TYPE)
        merged.setdefault(key, dict(row))
        kinds = types.setdefault(key, [])
        kind = collapse(row[PROVIDER_TYPE])
        if kind and kind not in kinds:
            kinds.append(kind)
    for key, row in merged.items():
        row[PROVIDER_TYPE] = "; ".join(types[key])
    return list(merged.values())


def parse_license(text: str | None) -> License | None:
    """The first licence in the cell, with the state its prefix names (Missouri otherwise)."""
    number = next((clean(part) for part in split_identifiers(text) if clean(part)), None)
    if number is None:
        return None
    if (found := _LICENSE_PREFIX.match(number)) and found[1] in _LICENSE_STATES:
        return License(number=found[2], state=found[1])
    return License(number=number, state=_STATE)


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    text = collapse(row["PROVIDER NAME"])
    if text is None:
        raise ParseError("row has no provider name")
    name = parse_name_field(text, organization_hint="," not in text, first_name_first=True)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    npis = parse_npi_list(row["NPI"])
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
        license=parse_license(row["LICENSE #"]),
        action=ExclusionAction.TERMINATION,
        effective_date=issues.date("TERM DATE", row["TERM DATE"], _DATE_FORMATS),
        reason=clean(row["TERMINATION REASON"]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
