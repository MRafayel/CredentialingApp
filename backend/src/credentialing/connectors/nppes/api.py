"""The NPPES API (CMS): the live NPI registry, asked one NPI or one search at a time.

Observed 2026-11-07 (seven requests from the owner's network): ``https://npiregistry.cms.hhs.gov
/api/?version=2.1&...`` over HTTP/2 behind CloudFront, ``cache-control: no-store``, no
rate-limit headers, no robots.txt (that path serves the web app's HTML).

- ``number=<npi>``: HTTP 200, ``{"result_count": 1, "results": [...]}``, 0.5 to 0.7 s, about
  1 to 1.5 KB. **A deactivated NPI answers ``{"result_count":0,"results":[]}``,
  byte-identical to an NPI never issued**: the API lists active NPIs only, so "no result"
  means "not active in NPPES today", and only the mirror (the deactivation file) can say
  which.
- Errors come as HTTP 200 with ``{"Errors": [{"description", "field", "number"}]}``; the
  ``number`` echoes the request's input and is never kept or logged.
- A result: ``number``, ``enumeration_type`` (``NPI-1`` individual, ``NPI-2`` organization),
  ``created_epoch`` and ``last_updated_epoch`` (milliseconds, as strings), ``basic``,
  ``addresses``, ``taxonomies``, ``identifiers``, ``other_names``, ``endpoints``,
  ``practiceLocations``. Dates in ``basic`` are ISO ``YYYY-MM-DD`` (the files use
  ``MM/DD/YYYY``); ``name_prefix`` and ``name_suffix`` are ``--`` when empty. A reactivated
  NPI shows no deactivation or reactivation date: the API drops them for active records.
- Addresses: ``address_purpose`` ``LOCATION`` (practice) or ``MAILING``, postal codes of 5 or
  9 digits without a hyphen, ``country_code``. Taxonomies: ``code``, ``desc``, ``primary``,
  ``state`` and ``license`` (both may be null), ``taxonomy_group`` (``""`` when none).
- Search: ``last_name=...&state=NY&limit=200`` gave 200 results, 240 KB, in 4.4 s. Documented
  parameters: ``number``, ``enumeration_type``, ``taxonomy_description``, ``first_name``,
  ``use_first_name_alias``, ``last_name``, ``organization_name``, ``address_purpose``,
  ``city``, ``state``, ``postal_code``, ``country_code``, ``limit`` (10 to 200, default 10)
  and ``skip``; a trailing ``*`` after two characters is a wildcard; ``state`` alone is
  refused. We never page.

Politeness: a 1 s pause before every request (the API documents no limit; this is ours), one
request per action, never polled. Each request is one snapshot: a search's ``source_url``
drops the query string (names are personal data, and snapshots are shared reference data); a
lookup's keeps ``number``, a public identifier. The answer's body is not kept: the mirror
holds what it says. ``practiceLocations``, ``identifiers`` and ``endpoints`` are not read.
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import date, timedelta
from typing import ClassVar, Final, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import (
    FetchedDocument,
    Fetcher,
    FetchFailure,
    FetchOutcome,
)
from credentialing.connectors.framework.snapshots import snapshot_from
from credentialing.connectors.nppes.reader import (
    address_fields,
    other_person_name,
    state_code,
    unique_names,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    NpiStatus,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    NpiAnswer,
    NpiRecord,
    SourceSnapshot,
)
from credentialing.domain.ports import NpiQuery
from credentialing.domain.types import FEDERAL, Npi

logger = logging.getLogger(__name__)

API_URL: Final = "https://npiregistry.cms.hhs.gov/api/"
"""The API's endpoint; every request adds its parameters as a query string."""

REGISTRY_PAGE: Final = "https://npiregistry.cms.hhs.gov/"
"""The registry's search page, the one people use."""

API_VERSION: Final = "2.1"
MIN_LIMIT: Final = 10
MAX_LIMIT: Final = 200
"""The API's own bounds on ``limit``; a search is never paged past one answer."""

LINE_HASH_PREFIX: Final = "api:"
"""Starts the ``line_hash`` of a record written from the API, so the next file load, whose
hashes are of file rows, rewrites it (and restores any field the API does not give)."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="nppes_api",
    credential_type=CredentialType.NPI,
    jurisdictions=frozenset({FEDERAL}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.OFFICIAL_API,
    poll_interval=timedelta(0),
    source_url=REGISTRY_PAGE,
    display_name="NPPES API",
    crawl_delay=timedelta(seconds=1),
    last_modified_is_meaningful=False,
)

_ENTITY_KINDS: Final = {"NPI-1": EntityKind.INDIVIDUAL, "NPI-2": EntityKind.ORGANIZATION}
_ENUMERATION_TYPES: Final = {kind: code for code, kind in _ENTITY_KINDS.items()}
_FLAGS: Final = {"YES": True, "NO": False}
_EMPTY: Final = frozenset({"", "--"})
_ERROR_TEXT_LIMIT: Final = 200
_ACTIVE: Final = "A"

type _Kind = Literal["lookup", "search"]


class ApiError(ParseError):
    """The API answered with ``Errors``: "API error: <description> (<field>)"."""


class NppesApiConnector:
    """Implements :class:`~credentialing.domain.ports.NpiRegistryConnector` over the NPPES API.

    Every request goes through the shared fetcher (crawl delay, one retry on a transport
    error, 403 and 429 ``BLOCKED``, 5xx ``SOURCE_UNAVAILABLE``) and becomes one snapshot; an
    answer that is not the API's JSON, or that carries ``Errors``, is ``FAILED``, never "no
    result".
    """

    CAPABILITIES: ClassVar[ConnectorCapabilities] = CAPABILITIES

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    async def lookup_npi(self, npi: Npi) -> NpiAnswer:
        """Ask the registry for ``npi``: its record when active, no result otherwise."""
        url = f"{API_URL}?{urlencode({'version': API_VERSION, 'number': npi})}"
        return await self._ask("lookup", url, snapshot_url=url, expected=npi)

    async def search_npis(self, query: NpiQuery, *, limit: int) -> NpiAnswer:
        """Ask the registry for the NPIs matching ``query``; ``limit`` is clamped to 10..200."""
        return await self._ask("search", search_url(query, limit=limit), snapshot_url=API_URL)

    async def _ask(
        self, kind: _Kind, url: str, *, snapshot_url: str, expected: Npi | None = None
    ) -> NpiAnswer:
        started = time.perf_counter()
        result = await self._fetcher.fetch(
            url,
            previous=None,
            change_signal=CAPABILITIES.change_signal,
            crawl_delay=CAPABILITIES.crawl_delay,
        )
        answer = await self._answer(kind, result, snapshot_url, expected)
        logger.info(
            "nppes api request source_id=%s kind=%s outcome=%s results=%s duration_ms=%d",
            CAPABILITIES.source_id,
            kind,
            answer.snapshot.outcome.value,
            answer.result_count if answer.result_count is not None else "-",
            (time.perf_counter() - started) * 1000,
        )
        return answer

    async def _answer(
        self, kind: _Kind, result: FetchOutcome, snapshot_url: str, expected: Npi | None
    ) -> NpiAnswer:
        if not isinstance(result, FetchedDocument):
            failure = (
                result
                if isinstance(result, FetchFailure)
                else FetchFailure(
                    outcome=Outcome.FAILED,
                    status=result.status,
                    note="reported unchanged to a request that was not conditional",
                    fetched_at=result.fetched_at,
                )
            )
            return NpiAnswer(snapshot=_failed(failure, snapshot_url))
        snapshot = snapshot_from(
            result, source_id=CAPABILITIES.source_id, url=snapshot_url, previous=None
        )
        try:
            count, records = await asyncio.to_thread(parse_answer, result.body, snapshot)
            if expected is not None and any(record.npi != expected for record in records):
                raise ParseError("the answer is for another NPI")
        except Exception as error:  # any mapping failure is this request's FAILED outcome
            if isinstance(error, ApiError):
                note = str(error)
            else:
                reason = str(error) if isinstance(error, ParseError) else type(error).__name__
                note = f"unreadable answer: {reason}"
            failure = FetchFailure(
                outcome=Outcome.FAILED,
                status=result.status,
                note=note,
                fetched_at=result.fetched_at,
            )
            return NpiAnswer(snapshot=_failed(failure, snapshot_url))
        completed = snapshot.model_copy(
            update={
                "source_url": snapshot_url,
                "last_modified": None,
                "row_count": count,
                "note": _note(kind, count),
            }
        )
        return NpiAnswer(snapshot=completed, records=records, result_count=count)


def search_url(query: NpiQuery, *, limit: int) -> str:
    """The search request for ``query``: only the fields it fills, escaped, and ``limit``
    clamped to the API's 10..200."""
    parameters = {
        "version": API_VERSION,
        "last_name": query.last_name,
        "first_name": query.first_name,
        "organization_name": query.organization_name,
        "enumeration_type": _ENUMERATION_TYPES.get(query.entity_kind)
        if query.entity_kind
        else None,
        "state": query.state,
        "city": query.city,
        "postal_code": query.postal_code,
        "limit": str(min(max(limit, MIN_LIMIT), MAX_LIMIT)),
    }
    return f"{API_URL}?{urlencode({k: v for k, v in parameters.items() if v})}"


def parse_answer(body: bytes, snapshot: SourceSnapshot) -> tuple[int, tuple[NpiRecord, ...]]:
    """The API's count and its results as records of ``snapshot``.

    Raises:
        ApiError: The body carries ``Errors``; the message keeps each description and
            field, never the echoed input.
        ParseError: The body is not the API's JSON, or a result does not map; messages name
            fields, never values.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        raise ParseError("not JSON") from None
    if not isinstance(payload, dict):
        raise ParseError("not a JSON object")
    if "Errors" in payload:
        raise ApiError(f"API error: {_errors(payload['Errors'])}")
    count, results = payload.get("result_count"), payload.get("results")
    if not isinstance(results, list):
        raise ParseError("no results")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ParseError("result_count is not a count")
    return count, tuple(_record(item, index, snapshot) for index, item in enumerate(results))


def _errors(errors: object) -> str:
    """Each error's description and field; the echoed ``number`` is left out."""
    items = errors if isinstance(errors, list) else []
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "unspecified")[:_ERROR_TEXT_LIMIT]
        field = item.get("field")
        parts.append(f"{description} ({str(field)[:_ERROR_TEXT_LIMIT]})" if field else description)
    return "; ".join(parts) or "unspecified"


class _Raw(BaseModel):
    """Base of the API's shapes: unknown keys ignored, ``""`` and ``--`` read as absent."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() in _EMPTY:
            return None
        return value.strip() if isinstance(value, str) else value


class _Basic(_Raw):
    status: str
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    name_prefix: str | None = None
    name_suffix: str | None = None
    credential: str | None = None
    sole_proprietor: str | None = None
    organization_name: str | None = None
    organizational_subpart: str | None = None
    parent_organization_legal_business_name: str | None = None
    authorized_official_first_name: str | None = None
    authorized_official_last_name: str | None = None
    authorized_official_middle_name: str | None = None
    authorized_official_name_prefix: str | None = None
    authorized_official_name_suffix: str | None = None
    authorized_official_title_or_position: str | None = None
    authorized_official_credential: str | None = None
    enumeration_date: date | None = None
    last_updated: date | None = None
    certification_date: date | None = None


class _Address(_Raw):
    address_purpose: str | None = None
    address_1: str | None = None
    address_2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country_code: str | None = None


class _Taxonomy(_Raw):
    code: str
    primary: bool = False
    license: str | None = None
    state: str | None = None
    taxonomy_group: str | None = None


class _OtherName(_Raw):
    organization_name: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None


class _Result(_Raw):
    number: str = Field(pattern=r"^[0-9]{10}$")
    enumeration_type: str
    basic: _Basic
    addresses: tuple[_Address, ...] = ()
    taxonomies: tuple[_Taxonomy, ...] = ()
    other_names: tuple[_OtherName, ...] = ()


def _record(item: object, index: int, snapshot: SourceSnapshot) -> NpiRecord:
    """One API result as the mirror's record."""
    where = f"result {index + 1}"
    try:
        result = _Result.model_validate(item)
    except ValidationError as error:
        raise ParseError(f"{where} does not validate ({_fields(error)})") from None
    kind = _ENTITY_KINDS.get(result.enumeration_type)
    if kind is None:
        raise ParseError(f"{where}: enumeration_type is not NPI-1 or NPI-2")
    basic = result.basic
    if basic.status != _ACTIVE:
        raise ParseError(f"{where}: status is not A")
    person = None
    if kind is EntityKind.INDIVIDUAL and (basic.last_name or basic.first_name):
        person = {
            "last": basic.last_name or "",
            "first": basic.first_name or "",
            "middle": basic.middle_name,
            "suffix": basic.name_suffix,
        }
    official = None
    if basic.authorized_official_last_name or basic.authorized_official_first_name:
        official = {
            "name": {
                "last": basic.authorized_official_last_name or "",
                "first": basic.authorized_official_first_name or "",
                "middle": basic.authorized_official_middle_name,
                "suffix": basic.authorized_official_name_suffix,
            },
            "prefix": basic.authorized_official_name_prefix,
            "title": basic.authorized_official_title_or_position,
            "credential": basic.authorized_official_credential,
        }
    other_names = unique_names(
        (
            *(
                other.organization_name
                or other_person_name(
                    other.last_name or "", other.first_name or "", other.middle_name or ""
                )
                for other in result.other_names
            ),
            basic.parent_organization_legal_business_name,
        )
    )
    data = {
        "npi": result.number,
        "entity_kind": kind,
        "status": NpiStatus.ACTIVE,
        "person_name": person,
        "name_prefix": basic.name_prefix if person else None,
        "credential": basic.credential,
        "organization_name": basic.organization_name if kind is EntityKind.ORGANIZATION else None,
        "other_names": other_names,
        "sole_proprietor": _FLAGS.get(basic.sole_proprietor or ""),
        "organization_subpart": _FLAGS.get(basic.organizational_subpart or ""),
        "enumeration_date": basic.enumeration_date,
        "last_updated": basic.last_updated,
        "certification_date": basic.certification_date,
        "mailing_address": _address(result.addresses, "MAILING"),
        "practice_address": _address(result.addresses, "LOCATION"),
        "taxonomies": [
            {
                "code": taxonomy.code,
                "primary": taxonomy.primary,
                "license_number": taxonomy.license,
                "license_state": state_code(taxonomy.state or ""),
                "group": taxonomy.taxonomy_group,
            }
            for taxonomy in result.taxonomies
        ],
        "authorized_official": official,
        "line_hash": line_hash(item),
        "snapshot_id": snapshot.id,
    }
    try:
        return NpiRecord.model_validate(data)
    except ValidationError as error:
        raise ParseError(f"{where} does not validate ({_fields(error)})") from None


def line_hash(item: object) -> str:
    """``api:`` and the SHA-256 of the result's canonical JSON (keys sorted, no spaces)."""
    canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return LINE_HASH_PREFIX + hashlib.sha256(canonical.encode()).hexdigest()


def _address(addresses: tuple[_Address, ...], purpose: str) -> dict[str, str | None] | None:
    """The first address of ``purpose`` in the mirror's shape, or None."""
    found = next((a for a in addresses if (a.address_purpose or "").upper() == purpose), None)
    if found is None:
        return None
    return address_fields(
        found.address_1 or "",
        found.address_2 or "",
        found.city or "",
        found.state or "",
        found.postal_code or "",
        found.country_code or "",
    )


def _fields(error: ValidationError) -> str:
    """The fields a validation error names, never their values."""
    return ", ".join(
        sorted(
            {
                ".".join(str(part) for part in e["loc"] if not isinstance(part, int)) or "result"
                for e in error.errors(include_input=False)
            }
        )
    )


def _note(kind: _Kind, count: int) -> str:
    if kind == "lookup":
        return "lookup: found" if count else "lookup: no result (not active in NPPES)"
    return f"search: {count} result{'' if count == 1 else 's'}"


def _failed(failure: FetchFailure, snapshot_url: str) -> SourceSnapshot:
    return snapshot_from(failure, source_id=CAPABILITIES.source_id, url=snapshot_url, previous=None)
