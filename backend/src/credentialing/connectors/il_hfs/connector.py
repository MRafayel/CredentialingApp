"""Connector for the Illinois HFS Office of Inspector General provider sanctions list.

Source facts (observed 2026-11-03 and 2026-11-04):

- Page: https://ilhfspartner3.dynamics365portals.us/sanctions/ (Microsoft Power Pages; no
  robots.txt, a 404). A plain GET answers 200 (about 228 KB, no validators) and sets session
  cookies. The page states, as server-rendered text, "Sanctions Last Updated: 10/13/2026
  12:34:33 PM" (an HTML comment says it is the newest row's creation time, on the portal's
  clock, taken as Central time) and "Active Sanctions Count: 3273". Its grid carries a
  base64 ``data-view-layouts`` attribute: two views, "Active Sanctions" (used here) and
  "Recent Additions", each with its columns, a sort expression, an encrypted
  ``Base64SecureConfiguration`` that differs on every page load, and the "Export Data" action
  (``ViewActionLinks`` type 8) with the export path ``/_services/download-as-excel/<id>``.
- The export takes three more requests in one cookie session: ``GET /_layout/tokenhtml``
  (an anti-forgery token, as a hidden input and a cookie of the same session); a ``POST`` of
  the view's configuration to the export path with that token in the
  ``__RequestVerificationToken`` header, answered ``{"success":true,"sessionKey":"..."}``
  (without the JSON ``Accept`` and ``Content-Type`` headers the server answers 500); and
  ``GET <export path>?key=<sessionKey>``, the workbook as an attachment. The export ignores
  paging and holds every row.
- Change signal ``API_METADATA`` with the page as the answer that describes the current file:
  the stamp moves on every addition and the count on every removal (a readmitted provider is
  removed; the page says no history is kept), so ``"<stamp>|<count>"`` is the edition token
  kept as the snapshot's ``etag``. A row edited in place changes neither and is picked up at
  the next addition. The stamp, converted from Central time to UTC, is the list's own date.
  An unchanged poll is one GET; a changed one is four requests. The workbook's bytes differ on
  every download (zip timestamps), so its hash is no change signal. Polled weekly with a 10 s
  pause before every request (our choice: the site declares none). The secure configuration,
  the token and the session key are never logged or stored.
- The workbook (xlsx, about 234 KB): one sheet "Active Sanctions", header on row 1,
  ``Provider Name, License #, NPI, Provider Type, Affiliation, Action Date, Action Type,
  Address, Address 2, City, State, Zip Code``. Its stylesheet has an empty fill, which
  ``framework/spreadsheet.py`` repairs. Dates are Excel dates.
- ``Provider Name``: upper case, no commas. People are written surname first ("SAMPLEPERSON
  AVERY Q", "SAMPLE-PERSON AVERY"), a few with a credential or generational suffix inside the
  name ("SAMPLEPERSON M.D. AVERY", "SAMPLEPERSON JR AVERY"), which is taken out and kept as the
  suffix (``names.split_credentials``). Organizations are business names. ``Provider Type``
  decides only for facility and company types (:data:`ORGANIZATION_TYPES`): the name is the
  organization unless it ends with a personal credential, and a professional corporation
  ("AVERY Q SAMPLEPERSON PSYD PC") stays the organization as written. Under every other type
  (professions, "WAIVER SERVICE PROVIDER", empty, and types that list both people and
  businesses such as "DENTAL" or "BEHAVIOR HEALTH SERVICES") business words decide, otherwise
  the name is a person, surname first. A name's "D/B/A/" is read as "D/B/A".
- ``Affiliation`` (rows without a provider type, mostly "Barred" people): the business the
  party acted through, kept as a related party; an affiliation equal to the name is not.
- ``License #``: Illinois licence numbers in several shapes (nine digits without separators,
  "125.077040", "036-123456", "MD44816"), kept as written with state IL. The list is
  Illinois'; a licence from another state cannot be told apart (an assumption).
- ``NPI``: ten digits or empty. No state provider number column.
- ``Action Type``: "TERMINATED" (termination), "Barred" (debarment), "EXCLUSION",
  "SUSPENDED", "VOLUNTARY WITHDRAWAL" (other), kept as ``action_raw``. ``Action Date`` is the
  effective date. No end date and no reason: a provider leaves the list by being removed.
- Address from the five columns; a ZIP written "60601 - 1234" is read as ZIP+4, a "?" state
  as none.
- Rows repeating one identity (one person barred three times on one date, once per business)
  are merged, keeping every affiliation as a related party.
- Live run 2026-11-04 (edition stamped 10/13/2026 12:34:33 PM, count 3,273; four requests,
  page 228,570 bytes, workbook 234,006 bytes): 3,273 rows, 3,268 records, none skipped, 5
  merged, no unreadable values. 2,716 individuals and 552 organizations; termination 2,480,
  debarment 502, other 174, exclusion 65, suspension 47; licence on 1,439; NPI on 711;
  address on 2,939 (239 outside Illinois); related parties on 462 records; aliases on 7;
  28 suffixes, 11 of them taken from inside the name.
"""

import base64
import binascii
import html
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Final
from urllib.parse import urljoin
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from credentialing.connectors.framework.base import BulkListConnectorBase, Located, Metadata
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import (
    FetchFailure,
    FetchOutcome,
    Response,
    Session,
    SessionFetcher,
    document_from,
)
from credentialing.connectors.framework.names import (
    CORPORATE_DESIGNATORS,
    ParsedName,
    parse_name_field,
    split_aliases,
    split_credentials,
)
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    build_records,
    merge_duplicates,
)
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    collapse,
    parse_address,
    parse_npi,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    ExclusionAction,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    SourceSnapshot,
)

PROVIDER_NAME: Final = "Provider Name"
LICENSE: Final = "License #"
NPI: Final = "NPI"
PROVIDER_TYPE: Final = "Provider Type"
AFFILIATION: Final = "Affiliation"
ACTION_DATE: Final = "Action Date"
ACTION_TYPE: Final = "Action Type"
ZIP_CODE: Final = "Zip Code"

COLUMNS: Final = (
    PROVIDER_NAME,
    LICENSE,
    NPI,
    PROVIDER_TYPE,
    AFFILIATION,
    ACTION_DATE,
    ACTION_TYPE,
    "Address",
    "Address 2",
    "City",
    "State",
    ZIP_CODE,
)

JOINED: Final = (AFFILIATION, PROVIDER_TYPE, "Address", "Address 2", "City", "State", ZIP_CODE)
"""Raw columns that keep every distinct value when rows of one identity are merged."""

ACTIONS: Final = (
    ("terminat", ExclusionAction.TERMINATION),
    ("barr", ExclusionAction.DEBARMENT),
    ("exclu", ExclusionAction.EXCLUSION),
    ("suspen", ExclusionAction.SUSPENSION),
)
"""``Action Type`` words, first match wins; anything else ("VOLUNTARY WITHDRAWAL") is other."""

ORGANIZATION_TYPES: Final = frozenset(
    {
        "AMBULANCE SERV PROV", "ASSISTIVE EQUIPMENT", "CLINIC", "DME/SUPPLIES",
        "FED QLFY HEALTH CNTR", "GENERAL HOSPITAL", "GROUP", "HOME HEALTH",
        "HOME HEALTH AGENCY", "HOSPICE", "HOSPITAL", "INDEPENDENT LAB",
        "INDEPNDENT DIAGNOSTIC TESTING FAC", "LABORATORY", "LTC FACILITY", "MEDICAR PROVIDER",
        "MENTALLY RETRD FCLTY", "MH SERVICES PROVIDR", "NURSING FACILITIES", "OPTOMETRY - FAO",
        "PHARMACY", "RURAL HEALTH CLINIC", "TAXICAB / LIVERY CO", "TRANSPORTATION - AA",
    }
)  # fmt: skip
"""Provider types of facilities and companies, as the list spells them (2026-11-04). Types that
list people as well as businesses (DENTAL, BEHAVIOR HEALTH SERVICES, SOCIAL SERVICES - AA,
OTH TRAN PROV-NO-REG, TBI HOME/CMNTY MEDC, NURSING) are left to the name."""

SOURCE_URL: Final = "https://ilhfspartner3.dynamics365portals.us/sanctions/"
"""The sanctions page: the list's stamp, count and export configuration."""

TOKEN_URL: Final = urljoin(SOURCE_URL, "/_layout/tokenhtml")
"""The portal's anti-forgery token, for the export request."""

VIEW_NAME: Final = "Active Sanctions"
"""The grid view whose export is the list."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="il_hfs",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({"IL"}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.API_METADATA,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=SOURCE_URL,
    display_name="Illinois HFS OIG Provider Sanctions List",
    provider_type_fields=(PROVIDER_TYPE,),
    crawl_delay=timedelta(seconds=10),
)

_STATE: Final = "IL"
_PORTAL_TIME: Final = ZoneInfo("America/Chicago")
_STAMP_FORMAT: Final = "%m/%d/%Y %I:%M:%S %p"
_STAMP: Final = re.compile(
    r"Sanctions Last Updated:\s*(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)",
    re.IGNORECASE,
)
_COUNT: Final = re.compile(r"Active Sanctions Count:\s*(\d+)", re.IGNORECASE)
_LAYOUTS: Final = re.compile(r"""data-view-layouts\s*=\s*(?:'([^']*)'|"([^"]*)")""")
_TOKEN: Final = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_EXPORT_LINK_TYPE: Final = 8
_ZIP_HYPHEN: Final = re.compile(r"\s*-\s*")
_DBA_SLASH: Final = re.compile(r"(?<![A-Z0-9])D/B/A/", re.IGNORECASE)
_DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y")
_REFERER: Final = {"Referer": SOURCE_URL}
_AJAX: Final = {"X-Requested-With": "XMLHttpRequest", **_REFERER}
_EXPORT_HEADERS: Final = {
    **_AJAX,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json; charset=UTF-8",
}

_VIEW_NAME_PARAMETER: Final = "viewName"
_SORT_PARAMETER: Final = "sortExpression"
_CONFIGURATION_PARAMETER: Final = "base64SecureConfiguration"
_COLUMNS_PARAMETER: Final = "columns"


class _Url(BaseModel):
    path: str = Field(alias="Path", min_length=1)


class _ActionLink(BaseModel):
    type: int | None = Field(default=None, alias="Type")
    url: _Url | None = Field(default=None, alias="URL")


class _Configuration(BaseModel):
    view_action_links: list[_ActionLink] | None = Field(default=None, alias="ViewActionLinks")


class _Layout(BaseModel):
    view_name: str = Field(alias="ViewName")
    sort_expression: str | None = Field(default=None, alias="SortExpression")
    secure_configuration: str = Field(alias="Base64SecureConfiguration", min_length=1)
    columns: list[JsonValue] = Field(alias="Columns", min_length=1)
    configuration: _Configuration = Field(alias="Configuration")


_LAYOUT_LIST: Final = TypeAdapter(list[_Layout])


class IlHfsConnector(BulkListConnectorBase):
    """Reads the sanctions page's stamp and count; on a new edition exports the "Active
    Sanctions" view through the portal's token flow and maps each row."""

    metadata_url = SOURCE_URL

    def __init__(self, fetcher: SessionFetcher) -> None:
        super().__init__(fetcher)
        self._sessions = fetcher

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def read_metadata(self, body: bytes) -> Metadata:
        """The edition (stamp and count), the list date and the export configuration.

        Raises:
            ParseError: The page lacks the stamp, the count, or the view and its export.
        """
        page = body.decode("utf-8", errors="replace")
        stamp = _STAMP.search(page)
        if stamp is None:
            raise ParseError("no 'Sanctions Last Updated' stamp on the page")
        count = _COUNT.search(page)
        if count is None:
            raise ParseError("no 'Active Sanctions Count' on the page")
        stamp_text = collapse(stamp[1]) or ""
        layout = _active_layout(page)
        return Metadata(
            url=urljoin(SOURCE_URL, _export_path(layout)),
            version=f"{stamp_text}|{count[1]}",
            last_modified=parse_stamp(stamp_text),
            parameters={
                _VIEW_NAME_PARAMETER: layout.view_name,
                _SORT_PARAMETER: layout.sort_expression or "",
                _CONFIGURATION_PARAMETER: layout.secure_configuration,
                _COLUMNS_PARAMETER: json.dumps(layout.columns),
            },
        )

    async def download(
        self, location: Located, previous: SourceSnapshot | None, *, change_signal: ChangeSignal
    ) -> FetchOutcome:
        """The export: the anti-forgery token, the export request, then the file, in one
        session. Every failure is an outcome; its note names the step, never a value."""
        async with self._sessions.session(crawl_delay=CAPABILITIES.crawl_delay) as session:
            try:
                body = _export_body(location.parameters)
            except KeyError, ValueError:
                return FetchFailure(
                    outcome=Outcome.FAILED,
                    status=None,
                    note="export configuration missing from the page",
                    fetched_at=session.opened_at,
                )
            return await _export(session, location.url, body)

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row of the export; rows repeating an identity are merged."""
        rows = read_sheet(body, sheet=0, expected_columns=COLUMNS)
        parsed = build_records(rows, partial(_record, snapshot))
        records = merge_duplicates(parsed.records, joined=JOINED)
        return replace(parsed, records=records, merged=len(parsed.records) - len(records))


def parse_stamp(text: str) -> datetime:
    """The page's "Sanctions Last Updated" stamp (portal time, Central) in UTC.

    Raises:
        ParseError: The text is not a stamp.
    """
    try:
        local = datetime.strptime(text, _STAMP_FORMAT).replace(tzinfo=_PORTAL_TIME)
    except ValueError as error:
        raise ParseError("the 'Sanctions Last Updated' stamp is not a date") from error
    return local.astimezone(UTC)


def parse_party(name_cell: str, provider_type: str) -> ParsedName:
    """The party of a row: person or organization by the module's rules."""
    name = collapse(_DBA_SLASH.sub("D/B/A", name_cell))
    if name is None:
        raise ParseError("row has no provider name")
    facility = (collapse(provider_type) or "").upper() in ORGANIZATION_TYPES
    listed = parse_name_field(name, organization_hint=facility)
    if listed.person is None:
        return listed
    if _is_corporation(listed):
        if not facility:
            return listed
        # Under a facility type a professional corporation is the organization, as written.
        primary, marked = split_aliases(name)
        return ParsedName(person=None, organization=primary or name, aliases=marked)
    rest, credentials = split_credentials(name)
    if credentials is None:
        return listed
    reparsed = parse_name_field(rest)
    if reparsed.person is None:
        return listed
    person = reparsed.person.model_copy(update={"suffix": credentials})
    return ParsedName(person=person, organization=None, aliases=reparsed.aliases)


def parse_zip(cell: str) -> str:
    """A ZIP cell with the spaces around its hyphen removed ("60601 - 1234" is "60601-1234")."""
    return _ZIP_HYPHEN.sub("-", cell.strip())


def _is_corporation(parsed: ParsedName) -> bool:
    """Whether ``parse_name_field`` read a professional corporation ("... MD PC") as its
    person: the suffix then holds a corporate designator."""
    suffix = parsed.person.suffix if parsed.person else None
    words = {word.replace(".", "").upper() for word in (suffix or "").split()}
    return not words.isdisjoint(CORPORATE_DESIGNATORS)


def _active_layout(page: str) -> _Layout:
    found = _LAYOUTS.search(page)
    if found is None:
        raise ParseError("no grid view layouts on the page")
    encoded = html.unescape(found[1] if found[1] is not None else found[2])
    try:
        layouts = _LAYOUT_LIST.validate_json(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError, ValidationError) as error:
        raise ParseError("the grid view layouts are unreadable") from error
    for layout in layouts:
        if layout.view_name == VIEW_NAME:
            return layout
    raise ParseError(f"no {VIEW_NAME!r} view on the page")


def _export_path(layout: _Layout) -> str:
    for link in layout.configuration.view_action_links or ():
        if link.type == _EXPORT_LINK_TYPE and link.url is not None:
            return link.url.path
    raise ParseError(f"the {VIEW_NAME!r} view has no export action")


def _export_body(parameters: Mapping[str, str]) -> dict[str, object]:
    """The export request the page's own "Export Data" button sends.

    Raises:
        KeyError, ValueError: A parameter is missing or the columns are not JSON.
    """
    return {
        "viewName": parameters[_VIEW_NAME_PARAMETER],
        "columns": json.loads(parameters[_COLUMNS_PARAMETER]),
        "base64SecureConfiguration": parameters[_CONFIGURATION_PARAMETER],
        "sortExpression": parameters[_SORT_PARAMETER] or None,
        "search": None,
        "filter": None,
        "metaFilter": None,
        "nlSearchFilter": None,
        "page": 1,
        "pageSize": 100,
        "timezoneOffset": 0,
        "isDSTObserved": False,
    }


async def _export(session: Session, export_url: str, body: dict[str, object]) -> FetchOutcome:
    """The three export requests; the document is stamped with the export's own URL."""
    token_page = await session.request("GET", TOKEN_URL, headers=_AJAX)
    if isinstance(token_page, FetchFailure):
        return _step(token_page, "anti-forgery token")
    token = _TOKEN.search(token_page.body.decode("utf-8", errors="replace"))
    if token is None:
        return _failed(session, token_page, "no anti-forgery token")
    headers = {**_EXPORT_HEADERS, "__RequestVerificationToken": token[1]}
    answer = await session.request("POST", export_url, headers=headers, json=body)
    if isinstance(answer, FetchFailure):
        return _step(answer, "export request")
    key = _session_key(answer.body)
    if key is None:
        return _failed(session, answer, f"export not accepted (HTTP {answer.status})")
    file = await session.request("GET", export_url, headers=_REFERER, params={"key": key})
    if isinstance(file, FetchFailure):
        return _step(file, "export download")
    if "spreadsheetml" not in (file.content_type or "").lower():
        return _failed(session, file, "export download is not a workbook")
    return replace(document_from(file, session.opened_at), final_url=export_url)


def _session_key(body: bytes) -> str | None:
    """The export's session key from ``{"success": true, "sessionKey": "..."}``, or None."""
    try:
        answer = json.loads(body)
    except ValueError:
        return None
    if not isinstance(answer, dict) or answer.get("success") is not True:
        return None
    key = answer.get("sessionKey")
    return key if isinstance(key, str) and key.strip() else None


def _step(failure: FetchFailure, step: str) -> FetchFailure:
    return replace(failure, note=f"{step}: {failure.note}")


def _failed(session: Session, response: Response, note: str) -> FetchFailure:
    return FetchFailure(
        outcome=Outcome.FAILED, status=response.status, note=note, fetched_at=session.opened_at
    )


def _same_name(left: str, right: str) -> bool:
    return (collapse(left) or "").casefold() == (collapse(right) or "").casefold()


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    party = parse_party(row[PROVIDER_NAME], row[PROVIDER_TYPE])
    affiliation = collapse(row[AFFILIATION])
    related = (
        (affiliation,) if affiliation and not _same_name(affiliation, row[PROVIDER_NAME]) else ()
    )
    licence = clean(row[LICENSE])
    action_raw = clean(row[ACTION_TYPE])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=party.entity_kind,
        person_name=party.person,
        organization_name=party.organization,
        aliases=party.aliases,
        related_parties=related,
        npi=parse_npi(row[NPI]),
        license=License(number=licence, state=_STATE) if licence else None,
        address=parse_address(
            line1=row["Address"],
            line2=row["Address 2"],
            city=row["City"],
            state=row["State"],
            postal_code=parse_zip(row[ZIP_CODE]),
        ),
        action=classify_action(action_raw, ACTIONS, default=ExclusionAction.OTHER),
        action_raw=action_raw,
        effective_date=issues.date(ACTION_DATE, row[ACTION_DATE], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
