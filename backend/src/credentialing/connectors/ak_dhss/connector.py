"""Connector for the Alaska Department of Health excluded provider list.

Source facts (observed 2026-11-04):

- Landing page: https://health.alaska.gov/en/office-of-the-commissioner/medicaid-program-integrity/
  (Umbraco). Its only document link is "Current Alaska Excluded provider list" at
  ``/media/<random folder>/exclusion-list-<month>-<year>.pdf``: a dated file name in a random
  media folder, so change signal ``LINK_DISCOVERY``, polled weekly, 10 s before every request.
  The old address ``/Commissioner/Documents/PDF/AlaskaExcludedProviderList.pdf`` redirects to
  a stale copy (June 2025) and is never used.
- Cloudflare's managed challenge (307, then 403 "Just a moment...", no checkbox) answers every
  URL of the host, to scripts and to Chrome alike, so every request goes straight to the
  headed browser (``access_policy`` BROWSER: no plain request is sent first, which would only
  knock for a 403 each week; owner, 2026-11-04): the landing page first, then the file with
  it as Referer (``browser_warm_up``).
  Trials of 2026-11-04: Chrome reporting ``navigator.webdriver`` loops the challenge and stays
  ``BLOCKED``; launched without its automation marker (``browser_hide_webdriver``) a blank
  profile cleared in 3 s (once in 111 s, hence a 150 s challenge wait), and a kept profile
  with the clearance cookie gets the page at once. A User-Agent override makes Cloudflare
  refuse, so Chrome's own identity stays. Nothing is ever clicked.
- The file: ``application/pdf`` shown inline (read by the in-page fetch), 931,355 bytes for
  the August 2026 edition, exported from Excel: 12 pages, the first only the agency
  letterhead with a printed "Revision Date" that is older than the list (not read as its
  date), then one ruled table per page with the header ``EXCLUSION DATE | LAST NAME | FIRST
  NAME | PROVIDER TYPE | EXCLUSION AUTHORITY | EXCLUSION REASON`` repeated. 305 table rows:
  5 drawn without their inner rules (split by ``framework/pdf_table.py``) and a last-row note
  "NOTE: * MEANS THE INDIVIDUAL/ENTITY IS REINSTATED". No NPI, licence, address, date of
  birth or end date.

Mapping:

- ``EXCLUSION DATE`` is M/D/YYYY; every row is an ``EXCLUSION``.
- A row whose ``FIRST NAME`` is "-" or empty is an organization named by ``LAST NAME`` (28
  rows, "SAMPLE HOME CARE, LLC", "417, LLC"), wrapped lines joined; a "DBA" name in it is an
  alias. Otherwise a person: a generational suffix comes off ``LAST NAME`` ("MOCK, JR"), the
  first word of ``FIRST NAME`` is the first name and the rest the middle name. A
  parenthesized part ("(aka MOCK)", "(MOCK)") is another name: in ``LAST NAME`` another
  surname ("MOCK, Avery Q"), in ``FIRST NAME`` other given names ("(aka Jay)" gives
  "SAMPLE, Jay"). A credential written before the given name ("DO Avery", 2 rows) is kept as
  written: the list does not say it is one.
- A leading "*" on ``LAST NAME`` marks a reinstated party (the file's note): it is taken off
  the name, kept in ``raw``, and the record says "Reinstated (*)" as published. The row stays
  listed, as for the other lists that keep reinstated parties, until the owner decides.
- ``reason`` is ``EXCLUSION REASON`` with ``EXCLUSION AUTHORITY`` (OIG, MFCU, PI, STATE, DSDS,
  SDS) after it in parentheses ("SSA Section 1128(a)(1) (OIG)"), or either alone; "-" is
  empty. ``PROVIDER TYPE`` is shown as written; beneficiary rows ("RECIPIENT/BENEFICIARY",
  4 rows) are kept, as New Hampshire's are.
- The note row is the file's footer, not a party, and is dropped before rows are counted.
- First sync 2026-11-04: see the docs.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import ParsedName, split_aliases, split_suffix
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    UnreadableRow,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.values import clean, collapse
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

EXCLUSION_DATE: Final = "EXCLUSION DATE"
LAST_NAME: Final = "LAST NAME"
FIRST_NAME: Final = "FIRST NAME"
PROVIDER_TYPE: Final = "PROVIDER TYPE"
EXCLUSION_AUTHORITY: Final = "EXCLUSION AUTHORITY"
EXCLUSION_REASON: Final = "EXCLUSION REASON"
COLUMNS: Final = (
    EXCLUSION_DATE,
    LAST_NAME,
    FIRST_NAME,
    PROVIDER_TYPE,
    EXCLUSION_AUTHORITY,
    EXCLUSION_REASON,
)

REINSTATED: Final = "Reinstated (*)"
"""``action_raw`` of a row whose last name carries the file's reinstatement asterisk."""

_DATE_FORMATS: Final = ("%m/%d/%Y",)
_STATE: Final = "AK"
_NONE: Final = "-"
"""What the list writes in an empty cell (an organization's first name, a missing type)."""
_ASTERISK: Final = "*"
_FOOTER: Final = "NOTE"
_FILE_LINK: Final = re.compile(r"/media/[A-Za-z0-9]+/[^\"']*exclusion[^\"']*\.pdf$", re.IGNORECASE)
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")

LANDING_URL: Final = (
    "https://health.alaska.gov/en/office-of-the-commissioner/medicaid-program-integrity/"
)
"""The Medicaid Program Integrity page that links the current list."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="ak_dhss",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.BROWSER,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Alaska Excluded Provider List",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
    browser_warm_up=True,
    browser_hide_webdriver=True,
    browser_challenge_timeout=timedelta(seconds=150),
)


class AkDhssConnector(BulkListConnectorBase):
    """Finds the current PDF on the Program Integrity page, downloads it and maps each row."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "Current Alaska Excluded provider list" PDF link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row but the footer note; rows repeating an identity are merged."""
        rows = _without_footer(read_pdf_table(body, expected_columns=COLUMNS))
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(
            parsed.records, joined=(PROVIDER_TYPE, EXCLUSION_AUTHORITY, EXCLUSION_REASON)
        )
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


def _without_footer(
    rows: Iterable[dict[str, str] | UnreadableRow],
) -> Iterator[dict[str, str] | UnreadableRow]:
    """The rows without the file's "NOTE: * MEANS ..." footer (a note, not a party)."""
    for row in rows:
        if (
            not isinstance(row, UnreadableRow)
            and row[EXCLUSION_DATE].upper().startswith(_FOOTER)
            and not row[LAST_NAME].strip()
            and not row[FIRST_NAME].strip()
        ):
            continue
        yield row


def is_reinstated(row: Mapping[str, str]) -> bool:
    """Whether the row's last name carries the file's reinstatement asterisk."""
    return row[LAST_NAME].strip().startswith(_ASTERISK)


def parse_party(row: Mapping[str, str]) -> ParsedName:
    """The row's party: an organization when the first name is "-" or empty, otherwise a
    person with the other names the parentheses give (module rules).

    Raises:
        ParseError: The row has no last name.
    """
    last_cell = collapse(row[LAST_NAME].strip().removeprefix(_ASTERISK))
    if last_cell is None:
        raise ParseError("row has no last name")
    first_cell = collapse(row[FIRST_NAME])
    if first_cell is None or first_cell == _NONE:
        primary, others = split_aliases(last_cell)
        names = (primary, *others) if primary else others
        if not names:
            raise ParseError("an organization's row has no name")
        return ParsedName(person=None, organization=names[0], aliases=names[1:])
    last_text, other_surnames = _apart(last_cell)
    last, suffix = split_suffix(last_text)
    given_text, other_given = _apart(first_cell)
    given, *middle = given_text.split() or [""]
    if not last or not given:
        raise ParseError("a person's row lacks a last or a first name")
    person = PersonName(last=last, first=given, middle=" ".join(middle) or None, suffix=suffix)
    given_names = " ".join(given_text.split())
    aliases = [f"{surname}, {given_names}" for surname in other_surnames]
    aliases += [f"{last}, {names}" for names in other_given]
    return ParsedName(person=person, organization=None, aliases=tuple(dict.fromkeys(aliases)))


def _apart(cell: str) -> tuple[str, tuple[str, ...]]:
    """A name cell without its parenthesized and alias-marked parts, and those parts, each a
    name of its own ("(aka MOCK)" and "(MOCK)" give "MOCK"; "(ONE, TWO)" gives both)."""
    others: list[str] = []
    for inside in _PARENTHESIZED.findall(cell):
        primary, marked = split_aliases(inside)
        for text in (primary, *marked):
            others.extend(part.strip() for part in text.split(",") if part.strip())
    primary, marked = split_aliases(_PARENTHESIZED.sub(" ", cell))
    return primary, (*others, *marked)


def parse_reason(row: Mapping[str, str]) -> str | None:
    """The reason as written, with the authority after it in parentheses; either alone."""
    reason = _value(row[EXCLUSION_REASON])
    authority = _value(row[EXCLUSION_AUTHORITY])
    if reason is not None and authority is not None:
        return f"{reason} ({authority})"
    return reason or authority


def _value(text: str) -> str | None:
    cleaned = clean(text)
    return None if cleaned == _NONE else cleaned


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row)
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        action=ExclusionAction.EXCLUSION,
        action_raw=REINSTATED if is_reinstated(row) else None,
        effective_date=issues.date(EXCLUSION_DATE, row[EXCLUSION_DATE], _DATE_FORMATS),
        reason=parse_reason(row),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
