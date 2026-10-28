"""HTTP downloads for bulk-list connectors: change detection and failure mapping, written once.

One shared ``httpx2.AsyncClient`` (built by the composition root with the timeout, redirects
and the configured User-Agent, a standard browser string by default) is wrapped by
:class:`HttpFetcher`, which:

- skips the download when the source did not change, using the connector's change signal;
- caps the body size and hashes it;
- retries once, after a pause, on a connection error or timeout, and never on an HTTP status;
- translates every failure into a normalized outcome: timeouts, connection errors and 5xx are
  ``SOURCE_UNAVAILABLE``; 401, 403, 429 and challenge pages are ``BLOCKED``; anything else
  unexpected is ``FAILED``. A failure never looks like an empty list.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Final, Protocol

import httpx2

from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import SourceSnapshot
from credentialing.domain.ports import Clock

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS: Final = 1
"""Retries after a connection error or timeout. HTTP error statuses are never retried."""

RETRY_PAUSE_SECONDS: Final = 5.0
"""Pause before the retry, so a briefly overloaded server gets a moment."""

MAX_BODY_BYTES: Final = 50 * 1024 * 1024
"""Largest body accepted; a bigger one is ``FAILED`` rather than read into memory."""

_BLOCKING_STATUSES: Final = frozenset({401, 403, 429})
_CHALLENGE_SCAN_BYTES: Final = 64 * 1024
_CHALLENGE_MARKERS: Final = (
    b"/cdn-cgi/challenge-platform/",
    b"cf-chl-",
    b"cf-turnstile",
    b"_incapsula_resource",
    b"g-recaptcha",
    b"h-captcha",
    b"<title>just a moment...</title>",
    b"attention required! | cloudflare",
)


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """A new body from the source, with its hash and cache validators."""

    body: bytes
    content_hash: str
    etag: str | None
    last_modified: datetime | None
    content_type: str | None
    final_url: str
    byte_size: int
    fetched_at: datetime
    status: int


@dataclass(frozen=True, slots=True)
class NotModified:
    """The source is unchanged since the previous snapshot; ``reason`` says how we know."""

    reason: str
    etag: str | None
    last_modified: datetime | None
    fetched_at: datetime
    status: int


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """The source could not be read; ``outcome`` is never ``COMPLETED``."""

    outcome: Outcome
    status: int | None
    note: str
    fetched_at: datetime


type FetchOutcome = FetchedDocument | NotModified | FetchFailure


class Fetcher(Protocol):
    """What a bulk-list connector needs to download its source."""

    async def fetch(
        self, url: str, *, previous: SourceSnapshot | None, change_signal: ChangeSignal
    ) -> FetchOutcome:
        """Download ``url`` unless ``previous`` shows it unchanged, or report the failure."""
        ...


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    body: bytes
    etag: str | None
    last_modified: datetime | None
    content_type: str | None
    final_url: str


class HttpFetcher:
    """Implements :class:`Fetcher` over one shared ``httpx2.AsyncClient``."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        clock: Clock,
        *,
        retry_pause_seconds: float = RETRY_PAUSE_SECONDS,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self._client = client
        self._clock = clock
        self._retry_pause = retry_pause_seconds
        self._max_body_bytes = max_body_bytes

    async def fetch(
        self, url: str, *, previous: SourceSnapshot | None, change_signal: ChangeSignal
    ) -> FetchOutcome:
        """Fetch ``url`` with the change detection ``change_signal`` names.

        - ``CONDITIONAL_GET``: ``If-None-Match``/``If-Modified-Since`` from ``previous``; a 304
          is unchanged.
        - ``CONTENT_HASH``: a plain GET; a body hashing equal to ``previous`` is unchanged.
        - ``HEADER_COMPARE``: HEAD first; equal ETag/Last-Modified is unchanged, otherwise GET.

        After any GET, a body hashing equal to ``previous`` is reported as unchanged too, for
        servers that ignore conditional requests.
        """
        started = time.perf_counter()
        fetched_at = self._clock.now()
        match change_signal:
            case ChangeSignal.CONDITIONAL_GET:
                result = await self._get(url, previous, fetched_at, conditional=True)
            case ChangeSignal.CONTENT_HASH:
                result = await self._get(url, previous, fetched_at, conditional=False)
            case ChangeSignal.HEADER_COMPARE:
                result = await self._header_compare(url, previous, fetched_at)
            case ChangeSignal.API_METADATA | ChangeSignal.LINK_DISCOVERY:
                raise NotImplementedError(
                    f"{change_signal} needs a source-specific metadata request or link "
                    "extraction; no connector uses it yet"
                )
        logger.info(
            "http fetch url=%s signal=%s result=%s status=%s bytes=%s duration_ms=%d",
            url,
            change_signal.value,
            _describe(result),
            result.status,
            result.byte_size if isinstance(result, FetchedDocument) else "-",
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def _header_compare(
        self, url: str, previous: SourceSnapshot | None, fetched_at: datetime
    ) -> FetchOutcome:
        head = await self._request("HEAD", url, {}, fetched_at)
        if isinstance(head, FetchFailure):
            return head
        if previous is not None and _same_validators(previous, head):
            return NotModified(
                reason="validators unchanged",
                etag=head.etag,
                last_modified=head.last_modified,
                fetched_at=fetched_at,
                status=head.status,
            )
        return await self._get(url, previous, fetched_at, conditional=False)

    async def _get(
        self,
        url: str,
        previous: SourceSnapshot | None,
        fetched_at: datetime,
        *,
        conditional: bool,
    ) -> FetchOutcome:
        headers: dict[str, str] = {}
        if conditional and previous is not None:
            if previous.etag:
                headers["If-None-Match"] = previous.etag
            if previous.last_modified:
                headers["If-Modified-Since"] = _http_date(previous.last_modified)
        response = await self._request("GET", url, headers, fetched_at)
        if isinstance(response, FetchFailure):
            return response
        if response.status == 304:
            if not headers:
                return FetchFailure(
                    outcome=Outcome.FAILED,
                    status=304,
                    note="HTTP 304 to a request that was not conditional",
                    fetched_at=fetched_at,
                )
            return NotModified(
                reason="HTTP 304",
                etag=response.etag,
                last_modified=response.last_modified,
                fetched_at=fetched_at,
                status=304,
            )
        content_hash = hashlib.sha256(response.body).hexdigest()
        if previous is not None and previous.content_hash == content_hash:
            return NotModified(
                reason="content hash unchanged",
                etag=response.etag,
                last_modified=response.last_modified,
                fetched_at=fetched_at,
                status=response.status,
            )
        return FetchedDocument(
            body=response.body,
            content_hash=content_hash,
            etag=response.etag,
            last_modified=response.last_modified,
            content_type=response.content_type,
            final_url=response.final_url,
            byte_size=len(response.body),
            fetched_at=fetched_at,
            status=response.status,
        )

    async def _request(
        self, method: str, url: str, headers: dict[str, str], fetched_at: datetime
    ) -> _Response | FetchFailure:
        """Send one request, retrying once on a transport error, and classify the answer."""
        attempts = RETRY_ATTEMPTS + 1
        for attempt in range(1, attempts + 1):
            try:
                async with self._client.stream(method, url, headers=headers) as response:
                    return await self._read(response, method, fetched_at)
            except httpx2.TransportError as error:
                if attempt < attempts:
                    logger.warning(
                        "http retry url=%s error=%s attempt=%d", url, type(error).__name__, attempt
                    )
                    await asyncio.sleep(self._retry_pause)
                    continue
                return FetchFailure(
                    outcome=Outcome.SOURCE_UNAVAILABLE,
                    status=None,
                    note=f"{type(error).__name__} after {attempts} attempts",
                    fetched_at=fetched_at,
                )
            except httpx2.HTTPError as error:
                return FetchFailure(
                    outcome=Outcome.FAILED,
                    status=None,
                    note=f"request failed: {type(error).__name__}",
                    fetched_at=fetched_at,
                )
        raise AssertionError("unreachable: the loop always returns")

    async def _read(
        self, response: httpx2.Response, method: str, fetched_at: datetime
    ) -> _Response | FetchFailure:
        status = response.status_code

        def failure(outcome: Outcome, note: str) -> FetchFailure:
            return FetchFailure(outcome=outcome, status=status, note=note, fetched_at=fetched_at)

        if response.headers.get("cf-mitigated", "").lower() == "challenge":
            return failure(Outcome.BLOCKED, f"challenge page (HTTP {status})")
        if status in _BLOCKING_STATUSES:
            retry_after = response.headers.get("retry-after")
            suffix = f", Retry-After {retry_after}" if retry_after else ""
            return failure(Outcome.BLOCKED, f"HTTP {status}{suffix}")
        if status >= 500:
            return failure(Outcome.SOURCE_UNAVAILABLE, f"HTTP {status}")
        if status != 304 and not 200 <= status < 300:
            return failure(Outcome.FAILED, f"unexpected HTTP {status}")

        body = b""
        if method != "HEAD" and status != 304:
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_body_bytes:
                return failure(Outcome.FAILED, f"body of {declared} bytes exceeds the cap")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > self._max_body_bytes:
                    return failure(
                        Outcome.FAILED, f"body exceeds the cap of {self._max_body_bytes} bytes"
                    )
            body = bytes(chunks)

        content_type = response.headers.get("content-type")
        if _is_challenge(content_type, body):
            return failure(Outcome.BLOCKED, f"challenge page (HTTP {status})")
        return _Response(
            status=status,
            body=body,
            etag=response.headers.get("etag"),
            last_modified=_parse_http_date(response.headers.get("last-modified")),
            content_type=content_type,
            final_url=str(response.url),
        )


def _is_challenge(content_type: str | None, body: bytes) -> bool:
    """Whether an HTML body is a bot-manager challenge or CAPTCHA page rather than data."""
    if not content_type or "html" not in content_type.lower():
        return False
    head = body[:_CHALLENGE_SCAN_BYTES].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _same_validators(previous: SourceSnapshot, response: _Response) -> bool:
    """True when at least one validator is on both sides and every shared one is equal."""
    compared = False
    if previous.etag and response.etag:
        if previous.etag != response.etag:
            return False
        compared = True
    if previous.last_modified and response.last_modified:
        if previous.last_modified != response.last_modified:
            return False
        compared = True
    return compared


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _http_date(moment: datetime) -> str:
    return format_datetime(moment.astimezone(UTC), usegmt=True)


def _describe(result: FetchOutcome) -> str:
    match result:
        case FetchedDocument():
            return "new"
        case NotModified():
            return "unchanged"
        case FetchFailure():
            return result.outcome.value
