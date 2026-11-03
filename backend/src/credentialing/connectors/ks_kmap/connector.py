"""Connector for the Kansas KDHE Terminated Provider List (KMAP, Kansas Medicaid).

The KDHE Division of Health Care Finance lists the providers "whose Medicaid provider
agreement has been terminated" (reasons in K.A.R. 30-5-60); listed providers may not be
reimbursed for Medicaid services in any capacity. Every row is a ``TERMINATION``.

Source facts (survey of 2026-10-16 to 2026-10-24, re-checked 2026-11-02):

- Landing page: https://www.kdhe.ks.gov/172/Medicaid ("Medicaid Program Integrity", section
  "Terminated Provider List": "not updated on a schedule, but it is updated as needed").
  CivicPlus behind Cloudflare. It links "Termination List (PDF)" (``DocumentCenter/View/516``,
  8 pages) and "Termination List (XLSX)" at the stable
  ``https://www.kdhe.ks.gov/DocumentCenter/View/517/Termination-List-XLSX``, which the
  connector downloads directly; the landing page is not requested.
- Access on 2026-11-02 (two visits, 12 minutes apart): the landing page answered a script
  with ``403``, a JavaScript challenge (``cf-mitigated: challenge``), and loaded in headed
  Chrome (200). The xlsx URL answered a plain script ``200`` (26,479 bytes) at 09:23 UTC,
  then ``403`` with the challenge at 09:35. Headed Chrome got Cloudflare's "Attention
  Required!" block page on the xlsx URL both times, not the file, and nothing cleared it
  within 45 to 90 s: at 09:23 after loading the landing page (200) in the same session, at
  09:35 sent straight to the file by the connector. On 2026-10-22 and 2026-10-24 headed
  Chrome loaded the landing page first (its challenge cleared, ``cf_clearance`` and
  ``__cf_bm`` cookies set) and then downloaded the file in the same session.
  ``browser_fallback`` repeats a refused (``BLOCKED``) request in headed Chrome, which waits
  for a challenge to clear by itself and never clicks; a page that stays a challenge or a
  block is ``BLOCKED``, never an empty list. ``browser_warm_up`` makes it follow the
  survey's order: the landing page (``source_url``) first, its challenge waited out, then
  its ``load`` event, the ``cf_clearance`` cookie (up to 30 s) and a 10 s dwell, then the
  file with the landing page as Referer: one extra page load per poll that reaches the
  browser. First syncs on 2026-11-02 (each one plain request, then the browser): at 09:54
  with a 2 s pause after the landing page, the landing page cleared and the file stayed
  blocked for 45 s; at 10:04, with the dwell, the landing page itself stayed a challenge
  (HTTP 403) for 45 s. Both ``BLOCKED``; nothing was clicked or retried, and a blocked poll is
  next due one poll interval later. robots.txt was refused to scripts, so no crawl delay is
  known: the connector waits 10 s before every request and polls weekly.
- On 2026-11-03 at 07:44 UTC the sync ended ``BLOCKED`` "challenge page not cleared on the
  entry page (HTTP 403)" although Cloudflare had set a fresh ``cf_clearance`` during the run:
  a challenge loop, a usual Cloudflare answer to a browser reporting ``navigator.webdriver``
  true. As a test the owner then had Chrome started with ``navigator.webdriver`` false (the
  launch argument ``--disable-blink-features=AutomationControlled``) and the Kansas cookies,
  local storage and network state cleared from the profile. That one attempt, 08:30 UTC
  (``make sync SOURCE=ks_kmap``), was ``COMPLETED``: plain HTTP 403 (challenge), then
  headed Chrome cleared the landing page, got ``cf_clearance`` and downloaded the file (200,
  26,479 bytes, browser run 26 s, sync 36 s), 213 rows, 213 records added, none skipped.
  The switch was rolled back the same day on the owner's decision, to test whether it was
  the cause. One attempt at 11:23 UTC with Chrome's own identity (``navigator.webdriver``
  true) and the profile kept as 08:30 left it (its ``cf_clearance`` and the site's cookies):
  ``BLOCKED`` "challenge page (HTTP 403); browser fallback: challenge page not cleared (HTTP
  403)". Plain HTTP 403; in Chrome the landing page cleared and Cloudflare issued a new
  ``cf_clearance`` (11:23:44), but the file stayed a challenge for the full 45 s (browser
  run 75 s). A second attempt at 11:26 UTC from a blank profile (the old one moved aside)
  was ``BLOCKED`` the same way (new ``cf_clearance`` at 11:27:17, the file a challenge for
  45 s). So a blank profile is not enough: of the three runs that day, only the one with
  ``navigator.webdriver`` false was served the file. The 213 records of 08:30 stay current;
  the next poll is due 2026-11-10.
- The xlsx is served ``inline`` with a file name carrying the upload time ("Termination list
  10212026..._202610220822197228.xlsx"), ``Cache-Control: public, max-age=30``, no ETag and a
  ``Last-Modified`` equal to the request time, so it says nothing: change signal
  ``CONTENT_HASH``, a GET each week compared by hash (the zip entries carry fixed 1980
  timestamps and the file name the upload time, so the stored file is served as uploaded and
  one edition should hash the same). The edition's real date is inside the file:
  "REVISED: October 21, 2026" in G1 and the workbook's modified property
  (``docProps/core.xml``, 2026-10-21T20:17Z). The HTTP Last-Modified is the request time, so
  ``last_modified_is_meaningful`` is off and the snapshot keeps none: a serving timestamp
  must not be shown as the date the source was updated.
- One sheet, "Terminations": "REVISED: <date>" in row 1, the header on row 2, with colons:
  ``Termination Date: | D/B/A Business Name: | Name: | Provider Type: | KMAP Provider #: |
  NPI #: | Comments:`` (an Excel table, A2:G216 on 2026-11-02). Termination dates are Excel
  dates (2010-09-30 to 2026-10-20); a date typed as text is read too, an unreadable one is
  counted, never guessed. No address, date of birth, licence or end date.
- ``Name`` is written first name first ("First M. Last", "First Last Jr.") for people and
  as the business name for organizations ("Sample Care, LLC"). Rule (``names.py``, first
  name first): a name with a business word or "&" is an organization, and so is a one-word
  name (every person on the list has a first and a last name); otherwise a person. A
  credential after a comma ("First Last, MD") is the suffix. Several names joined by " / "
  (19 rows, nurse aides and other unlicensed staff without NPI or KMAP number; mostly one
  first name with two surnames) are one party's other names: the first is the party and
  the others aliases, kept as written; one of the other kind (a business on a person's
  row) would be a related party. A parenthesized name is an alias.
- ``D/B/A Business Name`` (10 rows, all on a person's row) lists one or more businesses
  (" / " or commas between them; a lone ", Inc." stays with its name). On a person's row
  they are related parties, not aliases: three different people's rows carry the same four
  business names, so the businesses are parties of their own (the owner's business), not
  other names of each person. On an organization's row they would be aliases.
- ``KMAP Provider #`` is "N/A" (112 rows), a nine-digit number with a service-location
  letter ("123456789A"), or 14 digits (20 rows, four typed as numbers); some cells list
  several (";", ",", line breaks, "123456789A,B,C" for several locations, notes in
  parentheses). The first becomes ``state_provider_id``; the cell stays in ``raw``.
- ``NPI #`` is "N/A" on 94 rows; five cells hold several NPIs (";", spaces, line breaks,
  notes): every valid NPI, the first the record's NPI and the others related NPIs.
- ``Comments`` is the reason ("HHS-OIG excluded participation." on 123 rows, "Loss of
  license or other State action.", "Terminated under a separate Medicaid enrollment.",
  "Medicare Revocation."), kept as the record's reason. None names an end or a
  reinstatement; removal from the list is the only reinstatement signal.
- No row repeated an identity on 2026-11-02; rows that would are merged, their provider
  type, number, D/B/A and comment cells joined with "; ".
- Parsed 2026-11-02 (the 26,479-byte file of the "REVISED: October 21, 2026" edition, read
  offline from the first visit; the second visit's end-to-end fetch was ``BLOCKED``): 213
  rows and records, none skipped, merged or unreadable; 179 individuals and 34
  organizations; NPI on 119 (related NPIs on 4), KMAP number on 101, aliases on 20, related
  businesses on 10 (20 names), every row dated.
- First completed sync 2026-11-03 (the same 26,479 bytes, through the browser): 213 records,
  175 individuals and 38 organizations (four names more read as businesses since the
  business words grew on 2026-11-03; all 38 carry an NPI); NPI on 119 (related NPIs on 4),
  KMAP number on 101, aliases on 20, related parties on 10, every row dated.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import ParsedName, parse_name_field, split_suffix
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

TERMINATION_DATE: Final = "Termination Date:"
DBA: Final = "D/B/A Business Name:"
NAME: Final = "Name:"
PROVIDER_TYPE: Final = "Provider Type:"
KMAP_NUMBER: Final = "KMAP Provider #:"
NPI: Final = "NPI #:"
COMMENTS: Final = "Comments:"
COLUMNS: Final = (TERMINATION_DATE, DBA, NAME, PROVIDER_TYPE, KMAP_NUMBER, NPI, COMMENTS)

_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
_STATE: Final = "KS"
_NAME_SEPARATOR: Final = re.compile(r"\s+/\s*|\s*/\s+|[\n;]")
"""Between names in one cell: a slash with a space beside it (the "D/B/A" marker has none),
a line break or a semicolon."""
_DESIGNATOR_ONLY: Final = re.compile(
    r"^(?:INC|L\.?L\.?C|L\.?L\.?P|P\.?L\.?L\.?C|P\.?C|P\.?A|CORP|CO|LTD)\.?$", re.IGNORECASE
)
_KMAP_NUMBER: Final = re.compile(r"(?<![0-9])[0-9]{8,14}[A-Z]?(?![0-9A-Z])", re.IGNORECASE)

DOWNLOAD_URL: Final = "https://www.kdhe.ks.gov/DocumentCenter/View/517/Termination-List-XLSX"
"""The "Termination List (XLSX)" document, as the connector fetches it."""

LANDING_URL: Final = "https://www.kdhe.ks.gov/172/Medicaid"
"""The Medicaid Program Integrity page that links the list."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ks_kmap",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Kansas KDHE Terminated Provider List",
    download_url=DOWNLOAD_URL,
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
    browser_warm_up=True,
    last_modified_is_meaningful=False,
)


class KsKmapConnector(BulkListConnectorBase):
    """Downloads the Kansas termination workbook and maps each provider into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the "Terminations" sheet; rows repeating an identity are merged."""
        rows = read_sheet(body, sheet="Terminations", expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(
            parsed.records, joined=(PROVIDER_TYPE, KMAP_NUMBER, NPI, DBA, COMMENTS)
        )
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


@dataclass(frozen=True, slots=True)
class Party:
    """The listed party of a row, its other names and the other parties named with it."""

    name: ParsedName
    aliases: tuple[str, ...]
    related_parties: tuple[str, ...]


def parse_party(name_cell: str | None, dba_cell: str | None) -> Party:
    """The party from the ``Name`` and ``D/B/A Business Name`` cells (module rules).

    Raises:
        ParseError: The name cell is empty.
    """
    names = [name for name in map(collapse, _NAME_SEPARATOR.split(name_cell or "")) if name]
    if not names:
        raise ParseError("row has no name")
    first, *others = names
    party = parse_name(first)
    if party.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no name")
    aliases = list(party.aliases)
    related: list[str] = []
    for other in others:
        kind = parse_name(other).entity_kind
        (related if kind not in (party.entity_kind, EntityKind.UNKNOWN) else aliases).append(other)
    businesses = split_business_names(dba_cell)
    if party.entity_kind is EntityKind.ORGANIZATION:
        aliases.extend(businesses)
    else:
        related.extend(businesses)
    return Party(
        name=party,
        aliases=tuple(dict.fromkeys(aliases)),
        related_parties=tuple(dict.fromkeys(related)),
    )


def parse_name(text: str) -> ParsedName:
    """One name, first name first; a one-word name is a business, a credential after a
    comma the person's suffix."""
    one_word = len(text.split()) == 1
    parsed = parse_name_field(text, organization_hint=one_word, first_name_first=True)
    if parsed.person is None or "," not in text:
        return parsed
    head, suffix = split_suffix(text)
    if suffix is None:
        return parsed
    person = parse_name_field(head, first_name_first=True).person
    if person is None:
        return parsed
    return replace(parsed, person=person.model_copy(update={"suffix": suffix}))


def split_business_names(text: str | None) -> tuple[str, ...]:
    """The businesses in a D/B/A cell: split at " / ", line breaks, semicolons and commas,
    a piece that is only a corporate designator ("Inc.") kept with the name before it."""
    names: list[str] = []
    for part in _NAME_SEPARATOR.split(text or ""):
        for piece in map(clean, part.split(",")):
            if piece is None:
                continue
            if names and _DESIGNATOR_ONLY.match(piece):
                names[-1] = f"{names[-1]}, {piece}"
            else:
                names.append(piece)
    return tuple(dict.fromkeys(names))


def first_kmap_number(text: str | None) -> str | None:
    """The first KMAP provider number in the cell ("123456789A", 14 digits), upper-cased."""
    found = _KMAP_NUMBER.search(text or "")
    return found[0].upper() if found else None


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row[NAME], row[DBA])
    npis = parse_npi_list(row[NPI])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.name.entity_kind,
        person_name=party.name.person,
        organization_name=party.name.organization,
        aliases=party.aliases,
        related_parties=party.related_parties,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        state_provider_id=first_kmap_number(row[KMAP_NUMBER]),
        action=ExclusionAction.TERMINATION,
        effective_date=issues.date(TERMINATION_DATE, row[TERMINATION_DATE], _DATE_FORMATS),
        reason=clean(row[COMMENTS]),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
