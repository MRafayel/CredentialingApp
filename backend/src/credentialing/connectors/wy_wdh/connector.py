"""Connector for the Wyoming Department of Health Medicaid provider exclusion list.

Source facts (survey of 2026-10-16, re-checked and first sync 2026-10-31):

- Landing page: https://health.wyo.gov/healthcarefin/medicaid/ (WordPress, 200,546 bytes).
  The list is linked inside a toggle as "Wyoming Medicaid Provider Exclusion List
  (09/01/2026)" at ``/wp-content/uploads/2026/09/2026_09_01_-Wyoming-Medicaid-Exclusion-
  List.pdf``: the edition date is in the path and the name, so change signal
  ``LINK_DISCOVERY``, polled weekly. robots.txt asks ``Crawl-delay: 10``, which every request
  honours. The WordPress REST media API answers ``403`` and is not used.
- The file: PDF, 172,547 bytes, with ETag and Last-Modified (``304`` on a given URL).
- 6 landscape pages exported from Excel: a title row inside the grid ("Wyoming Medicaid
  Provider Exclusion List"), the header once on page 1 (``Last Name, First Name, Business
  Name, Provider Type, Provider Number, City, State, Exclusion Date``), one empty row at the
  end. 118 rows; 48 cells wrap.
- Parties: a row with only ``Business Name`` is an organization. A person's ``Last Name``
  may carry a former name in parentheses after or before it ("SAMPLE (MAIDEN)", "(MAIDEN)
  SAMPLE", "SAMPLE(MAIDEN)"): the alias "MAIDEN, First Middle" (``names.py`` rule 1).
  ``First Name`` may carry other names: "Avery a.k.a Jay" (the alias "SAMPLE, Jay"), "(aka
  Avery Mockwell)" (as written) or "(OTHERONE, OTHERTWO)" (former surnames, as above).
  ``Business Name`` on a person's row is an alias when it starts with a marker ("aka-Mock,
  Avery J", "a.k.a. Avery Mockwell", "aka: ONE, TWO" for former surnames) and otherwise
  another party: a related party, as in California (the person's own business, an employer,
  once a federal prison).
- ``Provider Type`` is free text (Registered Nurse, Training DD Waiver, C.N.A., ...) and twice
  names a licence ("CNA 12345 (WY) ...", ".../WY License # 12345").
- ``Provider Number`` is free text: "N/A" on 29 rows, Wyoming Medicaid provider IDs of nine
  digits (also written "1234567 00"), NPIs ("NPI 1000000000", "1000000000NPI"), licences of
  several states ("RN 12345 (WY) RN 012345678 (OR) ...", "Lic# 1234567-1234(UT)",
  "WYRN# 12345", "RN123456 CO", "CNA #12345", "License #WY12345") and bare numbers. Read as:
  every valid NPI (the first the record's NPI, the others related NPIs); the first nine-digit
  number outside a licence as the state provider ID; the first licence named by a credential
  (RN, LPN, CNA, APN, CNP, Lic, License), with the state in parentheses or after it, before
  the number ("License #WY12345") or before the credential ("WY RN#", "NV-RN:"), Wyoming
  otherwise; ``Provider Type`` is searched for a licence when ``Provider Number`` names none.
  Bare numbers ("20001"), numbers with only a state ("WY12345", "1234A(WY)") and other
  credentials (OT, POD) are not read as licences; the cell always stays in ``raw``.
- ``Exclusion Date`` is M/D/YYYY; every row is an ``EXCLUSION``. ``City`` and ``State``
  (another state's code on 22 rows) form the address.
- A person listed twice with a different ``Business Name`` (same name, number and date) is one
  record: the business names, provider types and numbers are joined with "; ".
- Known reading gaps on 2026-10-31 (rows skipped, counted): on 3 rows the grid lacks the
  rule left of ``Last Name``, so the reader returns that cell empty and the person has no
  last name; on 1 row the source itself shifts the name one column right (surname in ``First
  Name``, given names in ``Business Name``).
- Cloudflare fronts the landing page and answered three of five syncs on 2026-10-31 with a
  403 JavaScript challenge; the PDF under ``/wp-content/uploads/`` answered 200 to plain
  requests. ``browser_fallback`` repeats a refused request in headed Chrome, where the
  challenge clears by itself.
- First sync 2026-10-31: see the docs.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase, first_href
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    ParsedName,
    parse_name_field,
    split_aliases,
    split_suffix,
)
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.values import collapse, parse_address, parse_npi_list
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
    License,
    PersonName,
    SourceSnapshot,
)

LAST_NAME: Final = "Last Name"
FIRST_NAME: Final = "First Name"
BUSINESS_NAME: Final = "Business Name"
PROVIDER_TYPE: Final = "Provider Type"
PROVIDER_NUMBER: Final = "Provider Number"
EXCLUSION_DATE: Final = "Exclusion Date"
COLUMNS: Final = (
    LAST_NAME,
    FIRST_NAME,
    BUSINESS_NAME,
    PROVIDER_TYPE,
    PROVIDER_NUMBER,
    "City",
    "State",
    EXCLUSION_DATE,
)

_DATE_FORMATS: Final = ("%m/%d/%Y",)
_STATE: Final = "WY"
_FILE_LINK: Final = re.compile(r"/wp-content/uploads/\d{4}/\d{2}/[^/]*Exclusion-List\.pdf$")
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")
_WRAPPED_HYPHEN: Final = re.compile(r"-\s*\n\s*")
_MEDICAID_ID: Final = re.compile(r"(?<![\w#:.-])(\d{7}) ?(\d{2})(?![\w-])")
_LICENSE: Final = re.compile(
    r"(?<![A-Za-z])(?:(?P<prefix>[A-Z]{2})\s*-?\s*)?"
    r"(?P<kind>(?i:RN|LPN|CNA|APN|CNP|LIC(?:ENSE)?))"
    r"(?:\s+(?P<middle>[A-Z]{2}))?(?i:\s*LICENSE)?\s*[#.:]?\s*#?\s*"
    r"(?P<inner>[A-Z]{2})?(?P<number>\d+(?:-\d+)?[A-Z]?)(?!\w)"
    r"(?:\s*\(\s*(?P<state>[A-Z]{2})\s*\)|\s+(?P<after>[A-Z]{2})(?!\w))?"
)
"""A licence named by its credential: "RN 12345 (WY)", "WYRN# 12345", "RN SD LICENSE 9001",
"License #WY12345", "Lic# 1234567-1234(UT)", "RN123456 CO"."""

LANDING_URL: Final = "https://health.wyo.gov/healthcarefin/medicaid/"
"""The Medicaid page whose publications toggle links the current list."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="wy_wdh",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.LINK_DISCOVERY,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=LANDING_URL,
    display_name="Wyoming Medicaid Provider Exclusion List",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
    browser_fallback=True,
)


class WyWdhConnector(BulkListConnectorBase):
    """Finds the current PDF on the Medicaid page, downloads it and maps each table row."""

    landing_url = LANDING_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        """The "Wyoming Medicaid Provider Exclusion List" PDF link."""
        return first_href(html, _FILE_LINK)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row into an exclusion record; rows repeating an identity are merged."""
        rows = read_pdf_table(body, expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(
            parsed.records, joined=(BUSINESS_NAME, PROVIDER_TYPE, PROVIDER_NUMBER)
        )
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


def parse_party(row: Mapping[str, str]) -> ParsedName:
    """The row's party: an organization from a row with only a business name, otherwise a
    person with the aliases and related parties the name columns give (module rules)."""
    last = collapse(row[LAST_NAME])
    first = collapse(row[FIRST_NAME])
    business = collapse(row[BUSINESS_NAME])
    if last is None:
        if first is not None or business is None:
            raise ParseError("row has no last name")
        organization, *others = _business_names(business)
        return ParsedName(person=None, organization=organization, aliases=tuple(others))
    last, suffix = split_suffix(last)
    given_text = first or ""
    in_parentheses = _PARENTHESIZED.findall(given_text)
    given, other_given = split_aliases(_PARENTHESIZED.sub(" ", given_text))
    parsed = parse_name_field(f"{last}, {given}")
    if parsed.person is None:
        raise ParseError("a person's name reads as a business")
    person = parsed.person.model_copy(update={"suffix": suffix}) if suffix else parsed.person
    aliases = [*parsed.aliases]
    aliases += _other_names(in_parentheses, person, surnames=True)
    aliases += _other_names(other_given, person, surnames=False)
    related: tuple[str, ...] = ()
    if business is not None:
        primary, marked = split_aliases(business)
        if primary:
            related = _business_names(business)
        else:
            aliases += _other_names(marked, person, surnames=True)
    return ParsedName(
        person=person,
        organization=None,
        aliases=tuple(dict.fromkeys(aliases)),
        related_parties=related,
    )


def _business_names(text: str) -> tuple[str, ...]:
    """A business cell's names: the cell as written, or its parts when it names aliases."""
    primary, marked = split_aliases(text)
    if not marked:
        return (text,)
    return (primary, *marked) if primary else marked


def _other_names(texts: Iterable[str], person: PersonName, *, surnames: bool) -> list[str]:
    """Each text's names: a full name as written; a list of single words are other surnames
    ("MAIDEN, First Middle") or, when not ``surnames``, other given names ("LAST, Jay")."""
    given = " ".join(part for part in (person.first, person.middle) if part)
    names: list[str] = []
    for text in texts:
        primary, marked = split_aliases(text)
        for name in (primary, *marked) if primary else marked:
            parts = [part.strip() for part in name.split(",") if part.strip()]
            if not parts or any(len(part.split()) > 1 for part in parts):
                names.append(name)
            elif surnames:
                names.extend(f"{part}, {given}" for part in parts)
            else:
                names.extend(f"{person.last}, {part}" for part in parts)
    return names


@dataclass(frozen=True, slots=True)
class ProviderNumbers:
    """What a ``Provider Number`` cell identifies, as far as the module rules read it."""

    npis: tuple[str, ...]
    state_provider_id: str | None
    license: License | None


def parse_provider_numbers(number_cell: str, provider_type: str = "") -> ProviderNumbers:
    """The NPIs, the first Wyoming Medicaid ID and the first named licence (module rules)."""
    text = collapse(_WRAPPED_HYPHEN.sub("-", number_cell)) or ""
    licence = parse_license(text) or parse_license(
        collapse(_WRAPPED_HYPHEN.sub("-", provider_type))
    )
    medicaid = _MEDICAID_ID.search(_LICENSE.sub(" ", text))
    return ProviderNumbers(
        npis=parse_npi_list(text),
        state_provider_id=medicaid[1] + medicaid[2] if medicaid else None,
        license=licence,
    )


def parse_license(text: str | None) -> License | None:
    """The first licence named by a credential in the text, Wyoming's unless a state is named."""
    found = _LICENSE.search(text or "")
    if found is None:
        return None
    state = found["state"] or found["after"] or found["inner"] or found["middle"] or found["prefix"]
    kind = found["kind"].upper()
    return License(
        number=found["number"],
        state=state or _STATE,
        kind=None if kind.startswith("LIC") else kind,
    )


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row)
    numbers = parse_provider_numbers(row[PROVIDER_NUMBER], row[PROVIDER_TYPE])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        related_parties=party.related_parties,
        npi=numbers.npis[0] if numbers.npis else None,
        related_npis=numbers.npis[1:],
        state_provider_id=numbers.state_provider_id,
        license=numbers.license,
        address=parse_address(city=row["City"], state=row["State"]),
        action=ExclusionAction.EXCLUSION,
        effective_date=issues.date(EXCLUSION_DATE, row[EXCLUSION_DATE], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
