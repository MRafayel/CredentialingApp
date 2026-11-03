"""Connector for the Colorado HCPF Terminated Provider List.

The Department of Health Care Policy & Financing lists the providers whose participation in
Health First Colorado (Colorado's Medicaid program) "has been terminated for cause" (good
cause as defined at 10 CCR 2505-10, Section 8.076.1.7), so every row is a ``TERMINATION``.

Source facts (survey of 2026-10-16 to 2026-10-22, re-checked 2026-11-02):

- Landing page: https://hcpf.colorado.gov/provider-termination ("Provider Termination and
  Reinstatements", Drupal behind CloudFront). It links the list as "Terminated Provider List"
  at ``/sites/hcpf/files/Terminations%20for%20cause%20ef.%2010.9.26.xlsx`` (the edition date,
  M.D.YY, is in the file name), so change signal ``LINK_DISCOVERY``: one GET of the landing
  page per poll, a download only when the link changes. Polled weekly, 10 s before every
  request (no robots.txt crawl delay is known: robots.txt itself was refused to scripts).
- Access: from 2026-10-16 to 2026-10-22 CloudFront's AWS WAF answered scripts, robots.txt
  and headless Chrome with 403, and a headed Chrome passed. On 2026-11-02 the landing page
  (26,961 bytes, ETag and Last-Modified, ``max-age=900``) and the file both answered 200 to a
  plain script. ``browser_fallback`` repeats a refused (403) request in headed Chrome, where
  the WAF lets the page through; nothing is clicked.
- The file link redirects to ``/sites/g/files/lrnvjt4251/files/<same name>``; the download
  is an xlsx of 28,904 bytes (edition 10.9.26) with Last-Modified (2026-10-17) and no ETag.
- The same page links a separate "Reinstated Provider List"
  (``/sites/hcpf/files/Reinstated%20Providers%20ef.%2010.9.26.xlsx``). It is not loaded: the
  link pattern skips it. It could later close records; until then removal from the
  terminations list is the only reinstatement signal.
- One sheet, "Termination List"; the header on row 1: ``Provider Name, "Doing Business As"
  Name, NPI, Termination Authority, Termination Effective Date`` (a sixth, unnamed column is
  empty). 321 rows and one empty row on 2026-11-02. No address, date of birth, licence,
  provider type or end date.
- ``Provider Name`` holds people and organizations in one column: "LAST, FIRST M" or "Last,
  First" for most people, "First Last" without a comma for about 40, organizations by their
  business words ("..., LLC", "... Home Health Care"). Rule (``names.py``, first name first):
  a name with a business word or "&" is an organization; otherwise a person, "LAST, FIRST"
  when it has a comma and first name first when it does not (checked on 2026-11-02 against
  the given and family names already in the database: the comma-less names fit
  "First Last", none fit only "Last First"). A comma-less organization without a business
  word is read as a person (a known limit; the cell stays in ``raw``). The first load read
  three organizations named only by a specialty or "Living" ("... PHYSICAL MEDICINE",
  "... DIAGNOSTICS", "... Living Centers") as people; since 2026-11-03 those words are
  business words, so they and one "... Pediatrics" are organizations from the next parse of
  the list (a new edition or a forced sync), which closes their person rows and creates
  organization rows.
- ``"Doing Business As" Name`` is set on 15 rows (a single space on many others): another
  name of the same party, so an alias, for a person's row as for an organization's.
- ``NPI`` is text (two cells are numbers), "N/A" on 21 rows, and on 8 rows several NPIs
  joined by "&", ";" or ",": every valid NPI, the first the record's NPI and the others
  related NPIs.
- ``Termination Authority`` is the rule cited ("10 CCR 2505-10, Section 8.076.1.7.e", "42 CFR
  455.416(c)", spelled several ways), kept as written as the record's reason.
- ``Termination Effective Date`` is an Excel date on every row (2011-01-29 to 2026-08-03);
  a date typed as text (M/D/YYYY) is read too, and an unreadable one is counted, not guessed.
- A provider listed twice with one name, NPI and date (once with and once without a
  "Doing Business As" name; once fully repeated) is one record: the alias names are unioned
  and the "Doing Business As" and authority cells joined with "; ".
- First fetch 2026-11-02 (10.9.26 edition, end to end, both requests over plain HTTP, the
  browser not needed): 321 rows, 319 records, 0 skipped, 2 merged; 246 individuals and 73
  organizations; NPI on 299 records, related NPIs on 8, aliases on 16, all dated.
"""

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field, split_aliases
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import clean, collapse, parse_npi_list
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord, SourceSnapshot

PROVIDER_NAME: Final = "Provider Name"
DOING_BUSINESS_AS: Final = '"Doing Business As" Name'
NPI: Final = "NPI"
AUTHORITY: Final = "Termination Authority"
EFFECTIVE_DATE: Final = "Termination Effective Date"
COLUMNS: Final = (PROVIDER_NAME, DOING_BUSINESS_AS, NPI, AUTHORITY, EFFECTIVE_DATE)

_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
_STATE: Final = "CO"
_FILE_LINK: Final = re.compile(r"/files/[^/?#]*terminat[^/?#]*\.xlsx$", re.IGNORECASE)
"""The terminations workbook ("Terminations for cause ef. 10.9.26.xlsx"), never the
reinstated-providers one."""

LANDING_URL: Final = "https://hcpf.colorado.gov/provider-termination"
"""The "Provider Termination and Reinstatements" page that links the current workbook."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="co_hcpf",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Colorado HCPF Terminated Provider List",
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
)


class CoHcpfConnector(BulkListConnectorBase):
    """Finds the current workbook on the HCPF page, downloads it and maps each provider."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "Terminated Provider List" workbook link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row into a record; rows repeating an identity are merged."""
        rows = read_sheet(body, expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=(DOING_BUSINESS_AS, AUTHORITY))
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


def doing_business_as(text: str | None) -> tuple[str, ...]:
    """The names in a "Doing Business As" cell, without a leading alias marker."""
    primary, marked = split_aliases(text)
    return (primary, *marked) if primary else marked


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name = parse_name_field(collapse(row[PROVIDER_NAME]), first_name_first=True)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    aliases = tuple(dict.fromkeys((*name.aliases, *doing_business_as(row[DOING_BUSINESS_AS]))))
    npis = parse_npi_list(row[NPI])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=aliases,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        action=ExclusionAction.TERMINATION,
        effective_date=issues.date(EFFECTIVE_DATE, row[EFFECTIVE_DATE], _DATE_FORMATS),
        reason=clean(row[AUTHORITY]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
