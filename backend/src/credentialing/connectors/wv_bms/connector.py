"""Connector for the West Virginia BMS list of Medicaid provider exclusions and terminations.

Source facts (survey of 2026-10-16, re-checked and first sync 2026-10-31):

- Landing page: https://www.wvmmis.com/SitePages/Medicaid%20Provider%20SanctionedExclusion,
  on the WV MMIS portal run by Gainwell: an ASP.NET page with a Telerik grid (57,887 bytes;
  a plain GET with our User-Agent gets ``200``). The grid names the document as text, "WV
  Provider Exclusions and Terminations October 2026.pdf" on 2026-10-31 (one entry), with its
  size and modified time; the link itself is a ``__doPostBack``. The page's script opens
  ``/SharepointDownload.aspx?parent=<folder>&docname=<name>``, which now answers ``301`` to
  ``/SharepointDownload`` with the same query, so the connector builds that URL from the
  docname. The month in the docname changes with every edition: change signal
  ``LINK_DISCOVERY``, polled weekly. The footer links Gainwell's site terms, not read.
- The file: PDF, 210,445 bytes on 2026-10-31, no ETag or Last-Modified,
  ``Cache-Control: no-store``, so the docname is the only change signal.
- 13 landscape pages written by Google Docs: one ruled table with the header on every page
  (once more in the middle of page 13; later pages drop the slash in "Reason for Exclusion /
  Termination"): ``Last Name, First Name, NPI, Address 1, Address 2, City, State, ZIP,
  Action Date, Excluded / Terminated, Reason for Exclusion / Termination``. 166 rows; 252
  cells wrap over several lines.
- A row without a first name is an organization, named in ``Last Name`` (wrapped over up to
  three lines). A person's last name may end in a suffix ("Jr.", ", Jr", "II"). A last name
  that names a business ("SAMPLE & Associates", "SAMPLEPERSON MD Inc", one row each) is read
  with the first name in front: a professional corporation becomes that person with the
  corporation as an alias, any other business stays an organization (``names.py`` rules 2
  and 3). The renderer breaks long words inside narrow cells: in a name, a line that starts
  with a lower-case letter continues the word above ("Averyjam\\nes").
- ``NPI``: one number on most rows, "N/A" on 7, empty on 1, three NPIs on one organization's
  row and notes next to two numbers ("1000000000 - CMS Deactivated NPI"). Every valid NPI is
  kept: the first as the record's NPI, the others as related NPIs.
- Address: five columns. ZIP+4 wraps before or after its hyphen ("25000-\\n1234"). Prison
  addresses carry the inmate number in ``Address 2`` ("#12345-678"), stored as published.
  Other states than WV occur (OH, PA, MD, KY, VA and nine more).
- ``Action Date``: M/D/YYYY, some with leading zeros, M/D/YY on 3 rows. ``Excluded /
  Terminated``: "Excluded" or "Terminated", mostly followed by "Indefinitely" (no end date is
  published). Text naming an exclusion is an ``EXCLUSION`` (also when it names both), other
  text naming a termination a ``TERMINATION``; the text is kept as ``action_raw``. ``Reason
  for Exclusion / Termination`` (OIG, licence revoked by the WV Board of Medicine, ...) is the
  record's reason.
- Parsed 2026-10-31: 166 rows, 166 records, none skipped, no two rows with one identity (so
  nothing to merge): 154 individuals and 12 organizations (11 without a first name, one
  business named with its owner's first name); NPI on 158 (related NPIs on 1); address on
  every record, 32 outside WV, 54 with a second line; exclusion 70, termination 96; suffixes
  on 7 people; one alias (the professional corporation).
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from html import unescape
from typing import Final
from urllib.parse import quote
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    ParsedName,
    looks_like_business,
    parse_name_field,
    split_suffix,
)
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    collapse,
    parse_address,
    parse_npi_list,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    PersonName,
    SourceSnapshot,
)

LAST_NAME: Final = "Last Name"
FIRST_NAME: Final = "First Name"
ACTION_DATE: Final = "Action Date"
ACTION: Final = "Excluded / Terminated"
REASON: Final = "Reason for Exclusion / Termination"
COLUMNS: Final = (
    LAST_NAME,
    FIRST_NAME,
    "NPI",
    "Address 1",
    "Address 2",
    "City",
    "State",
    "ZIP",
    ACTION_DATE,
    ACTION,
    REASON,
)

ACTION_KEYWORDS: Final = (
    ("exclu", ExclusionAction.EXCLUSION),
    ("terminat", ExclusionAction.TERMINATION),
)
"""Keywords of the action column, first match wins: "Excluded / Terminated" is an exclusion."""

_DATE_FORMATS: Final = ("%m/%d/%Y", "%m/%d/%y")
_STATE: Final = "WV"
_WORD_CONTINUED: Final = re.compile(r"\n(?=[a-z])")
_WHITESPACE: Final = re.compile(r"\s+")
_DOCNAME: Final = re.compile(r">\s*(WV Provider Exclusions and Terminations [^<>]*?\.pdf)\s*<")

LANDING_URL: Final = "https://www.wvmmis.com/SitePages/Medicaid%20Provider%20SanctionedExclusion"
"""The portal page whose grid names the current document."""

DOWNLOAD_PREFIX: Final = (
    "https://www.wvmmis.com/SharepointDownload"
    "?parent=Medicaid+Provider+SanctionedExclusion&docname="
)
"""The download URL without its docname, as the page's script builds it (new path)."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="wv_bms",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="West Virginia Medicaid Provider Exclusions and Terminations",
)


class WvBmsConnector(BulkListConnectorBase):
    """Finds the current PDF in the portal's grid, downloads it and maps each table row."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The download URL of the first "WV Provider Exclusions and Terminations" document."""
        found = _DOCNAME.search(html)
        if found is None:
            raise ParseError("the grid names no WV Provider Exclusions and Terminations PDF")
        docname = _WHITESPACE.sub(" ", unescape(found[1])).strip()
        return DOWNLOAD_PREFIX + quote(docname)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every table row into an exclusion or termination record."""
        rows = read_pdf_table(body, expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def parse_party(row: Mapping[str, str]) -> ParsedName:
    """The row's party: an organization without a first name, otherwise a person."""
    last = name_text(row[LAST_NAME])
    if last is None:
        raise ParseError("row has no last or business name")
    first = name_text(row[FIRST_NAME])
    if first is None:
        return ParsedName(person=None, organization=last, aliases=())
    if looks_like_business(last):
        return parse_name_field(f"{first} {last}", first_name_first=True)
    last, suffix = split_suffix(last)
    given, *middle = first.split()
    person = PersonName(last=last, first=given, middle=" ".join(middle) or None, suffix=suffix)
    return ParsedName(person=person, organization=None, aliases=())


def name_text(text: str | None) -> str | None:
    """A wrapped name cell on one line; a line starting in lower case continues a word."""
    return collapse(_WORD_CONTINUED.sub("", text or ""))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row)
    npis = parse_npi_list(row["NPI"])
    action = collapse(row[ACTION])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        address=parse_address(
            line1=row["Address 1"],
            line2=row["Address 2"],
            city=row["City"],
            state=row["State"],
            postal_code=_WHITESPACE.sub("", row["ZIP"]),
        ),
        action=classify_action(action, ACTION_KEYWORDS, default=ExclusionAction.OTHER),
        action_raw=action,
        effective_date=issues.date(ACTION_DATE, row[ACTION_DATE], _DATE_FORMATS),
        reason=clean(row[REASON]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
