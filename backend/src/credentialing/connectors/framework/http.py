"""HTTP downloads for bulk-list connectors: change detection and failure mapping, written once.

One shared ``httpx2.AsyncClient`` (built by the composition root with the timeout, redirects
and the configured User-Agent, a standard browser string by default) is wrapped by
:class:`HttpFetcher`, which:

- skips the download when the source did not change, using the connector's change signal;
- caps the body size and hashes it;
- retries once, after a pause, on a connection error or timeout, and never on an HTTP status;
- waits the source's crawl delay (robots.txt ``Crawl-delay``) before every request it sends;
- translates every failure into a normalized outcome: timeouts, connection errors and 5xx are
  ``SOURCE_UNAVAILABLE``; 401, 403, 429, challenge pages and an HTML page served after a
  redirect to another host (a bot manager's validation page) are ``BLOCKED``; anything else
  unexpected is ``FAILED``. A failure never looks like an empty list.

Which URL to fetch, and whether to fetch at all, is the connector's business: link-discovery
and API-metadata sources resolve the file's URL first (``framework/base.py``) and then fetch
it here like any other.

A source whose file sits behind a token flow (a page's anti-forgery token, a POST that
prepares the export, a GET with the key it returns) opens a :class:`Session`: requests that
share one cookie jar, on a client of their own that is closed when the session ends, with the
same crawl delay, size cap and failure mapping. A POST is never repeated.

A file too large for memory (a dataset such as the NPPES registry, a gigabyte zipped) is
streamed to disk by :meth:`HttpFetcher.download` (:class:`FileFetcher`): the same change
detection, crawl delay, retry and failure mapping, the body hashed as it is written in chunks
off the event loop, a larger cap (:data:`MAX_FILE_BYTES`), and a partial file never left
behind.
"""

import asyncio
import hashlib
import json as json_module
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, Final, Protocol
from urllib.parse import urlsplit, urlunsplit

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

MAX_FILE_BYTES: Final = 2 * 1024**3
"""Largest file :meth:`HttpFetcher.download` writes to disk; a bigger one is ``FAILED``."""

FILE_CHUNK_BYTES: Final = 1024 * 1024
"""A downloaded file is written (and hashed) in chunks of this size, in a worker thread."""

_BLOCKING_STATUSES: Final = frozenset({401, 403, 429})
_REPEATABLE_METHODS: Final = frozenset({"GET", "HEAD"})
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
    fetched_in_browser: bool = False
    """The headed-browser fallback, not the HTTP client, read this body."""


@dataclass(frozen=True, slots=True)
class NotModified:
    """The source is unchanged since the previous snapshot; ``reason`` says how we know."""

    reason: str
    etag: str | None
    last_modified: datetime | None
    fetched_at: datetime
    status: int
    fetched_in_browser: bool = False
    """The headed-browser fallback, not the HTTP client, gave this answer."""


@dataclass(frozen=True, slots=True)
class FetchFailure:
    """The source could not be read; ``outcome`` is never ``COMPLETED``."""

    outcome: Outcome
    status: int | None
    note: str
    fetched_at: datetime


type FetchOutcome = FetchedDocument | NotModified | FetchFailure


@dataclass(frozen=True, slots=True)
class FetchedFile:
    """A new file from the source, written to ``path``, with its hash and cache validators."""

    path: Path
    content_hash: str
    etag: str | None
    last_modified: datetime | None
    content_type: str | None
    final_url: str
    byte_size: int
    fetched_at: datetime
    status: int


type FileOutcome = FetchedFile | NotModified | FetchFailure


class Fetcher(Protocol):
    """What a bulk-list connector needs to download its source."""

    async def fetch(
        self,
        url: str,
        *,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
        entry_url: str | None = None,
        profile: str | None = None,
        webdriver_disabled: bool = False,
        challenge_timeout: timedelta | None = None,
    ) -> FetchOutcome:
        """Download ``url`` unless ``previous`` shows it unchanged, or report the failure.

        ``crawl_delay`` is waited before every request sent. ``entry_url`` is a page to open
        first in the same session (the browser fallback's warm-up,
        ``ConnectorCapabilities.browser_warm_up``); a fetcher without sessions ignores it.
        The browser fallback's own options are ignored by a fetcher without a browser:
        ``profile`` (a folder of the browser profile kept for one source),
        ``webdriver_disabled`` (``ConnectorCapabilities.browser_webdriver_disabled``) and
        ``challenge_timeout`` (``ConnectorCapabilities.browser_challenge_timeout``, ``None``
        for the fetcher's default).
        """
        ...


@dataclass(frozen=True, slots=True)
class Response:
    """One answered request (2xx or 304), with the body and the headers the framework reads."""

    status: int
    body: bytes
    etag: str | None
    last_modified: datetime | None
    content_type: str | None
    final_url: str


def document_from(response: Response, fetched_at: datetime) -> FetchedDocument:
    """A new document from an answered request, hashed and sized."""
    return FetchedDocument(
        body=response.body,
        content_hash=hashlib.sha256(response.body).hexdigest(),
        etag=response.etag,
        last_modified=response.last_modified,
        content_type=response.content_type,
        final_url=response.final_url,
        byte_size=len(response.body),
        fetched_at=fetched_at,
        status=response.status,
    )


class Session(Protocol):
    """Requests that share one cookie jar, for a source whose file needs more than a GET."""

    @property
    def opened_at(self) -> datetime:
        """When the session opened: the ``fetched_at`` of its failures and of its document."""
        ...

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        json: object | None = None,
    ) -> Response | FetchFailure:
        """Send one request after the crawl delay and classify the answer like ``fetch``.

        ``json`` is sent as the body, serialized with :func:`json.dumps`. A transport error is
        retried once for GET and HEAD only; a POST is never repeated.
        """
        ...


class SessionFactory(Protocol):
    """Opens :class:`Session` s."""

    def session(self, *, crawl_delay: timedelta) -> AbstractAsyncContextManager[Session]:
        """A new session, with its own cookies, closed when the context ends."""
        ...


class SessionFetcher(Fetcher, SessionFactory, Protocol):
    """A fetcher that can also open sessions: what a connector with a token flow needs."""


class FileFetcher(Fetcher, Protocol):
    """A fetcher that can also stream a large file to disk: what a dataset connector needs."""

    async def download(
        self,
        url: str,
        *,
        destination: Path,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
    ) -> FileOutcome:
        """Write ``url`` to ``destination`` unless ``previous`` shows it unchanged, or report
        the failure; no file is left at ``destination`` unless the outcome is a
        :class:`FetchedFile`."""
        ...


class SourceFetcher(SessionFetcher, FileFetcher, Protocol):
    """Every kind of request the registered connectors make: what the registry is given."""


@dataclass(frozen=True, slots=True)
class _Transport:
    """Sending one request and classifying its answer, shared by the fetcher and sessions."""

    retry_pause: float
    max_body_bytes: int
    sleep: Callable[[float], Awaitable[None]]

    async def request(
        self,
        client: httpx2.AsyncClient,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        fetched_at: datetime,
        delay: float,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        retry: bool = True,
    ) -> Response | FetchFailure:
        """Send one request, retrying once on a transport error when ``retry``, and classify
        the answer.

        ``delay`` (the crawl delay, in seconds) is waited before each attempt.
        """
        attempts = RETRY_ATTEMPTS + 1 if retry else 1
        for attempt in range(1, attempts + 1):
            if delay > 0:
                await self.sleep(delay)
            try:
                async with client.stream(
                    method, url, headers=dict(headers), params=params, content=content
                ) as response:
                    return await self._read(response, method, url, fetched_at)
            except httpx2.TransportError as error:
                if attempt < attempts:
                    logger.warning(
                        "http retry url=%s error=%s attempt=%d",
                        loggable_url(url),
                        type(error).__name__,
                        attempt,
                    )
                    await self.sleep(self.retry_pause)
                    continue
                tries = f"after {attempts} attempts" if attempts > 1 else "(not repeated)"
                return FetchFailure(
                    outcome=Outcome.SOURCE_UNAVAILABLE,
                    status=None,
                    note=f"{type(error).__name__} {tries}",
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

    async def download(
        self,
        client: httpx2.AsyncClient,
        url: str,
        *,
        headers: Mapping[str, str],
        fetched_at: datetime,
        delay: float,
        destination: Path,
        max_file_bytes: int,
    ) -> _SavedFile | Response | FetchFailure:
        """GET ``url`` into ``destination`` in chunks, retrying once on a transport error.

        An answer that is not a file (a 304, or an HTML page that may be a challenge) is read
        and classified like :meth:`request` and returned as a :class:`Response`; a failure
        leaves no file behind.
        """
        for attempt in range(1, RETRY_ATTEMPTS + 2):
            if delay > 0:
                await self.sleep(delay)
            try:
                async with client.stream("GET", url, headers=dict(headers)) as response:
                    return await self._save(response, url, fetched_at, destination, max_file_bytes)
            except httpx2.TransportError as error:
                if attempt <= RETRY_ATTEMPTS:
                    logger.warning(
                        "http retry url=%s error=%s attempt=%d",
                        loggable_url(url),
                        type(error).__name__,
                        attempt,
                    )
                    await self.sleep(self.retry_pause)
                    continue
                return FetchFailure(
                    outcome=Outcome.SOURCE_UNAVAILABLE,
                    status=None,
                    note=f"{type(error).__name__} after {RETRY_ATTEMPTS + 1} attempts",
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

    async def _save(
        self,
        response: httpx2.Response,
        requested_url: str,
        fetched_at: datetime,
        destination: Path,
        max_file_bytes: int,
    ) -> _SavedFile | Response | FetchFailure:
        status = response.status_code
        refused = _refusal(response, fetched_at)
        if refused is not None:
            return refused
        content_type = response.headers.get("content-type")
        if status == 304 or _is_html(content_type):
            return await self._read(response, "GET", requested_url, fetched_at)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_file_bytes:
            return _failure(response, fetched_at, f"file of {declared} bytes exceeds the cap")
        sink = await asyncio.to_thread(_FileSink, destination)
        try:
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if sink.size + len(buffer) > max_file_bytes:
                    await asyncio.to_thread(sink.discard)
                    return _failure(
                        response, fetched_at, f"file exceeds the cap of {max_file_bytes} bytes"
                    )
                if len(buffer) >= FILE_CHUNK_BYTES:
                    await asyncio.to_thread(sink.write, bytes(buffer))
                    buffer.clear()
            if buffer:
                await asyncio.to_thread(sink.write, bytes(buffer))
            digest = await asyncio.to_thread(sink.finish)
        except BaseException:
            await asyncio.to_thread(sink.discard)
            raise
        return _SavedFile(
            status=status,
            content_hash=digest,
            byte_size=sink.size,
            etag=response.headers.get("etag"),
            last_modified=_parse_http_date(response.headers.get("last-modified")),
            content_type=content_type,
            final_url=str(response.url),
        )

    async def _read(
        self, response: httpx2.Response, method: str, requested_url: str, fetched_at: datetime
    ) -> Response | FetchFailure:
        status = response.status_code

        def failure(outcome: Outcome, note: str) -> FetchFailure:
            return FetchFailure(outcome=outcome, status=status, note=note, fetched_at=fetched_at)

        refused = _refusal(response, fetched_at)
        if refused is not None:
            return refused

        body = b""
        if method != "HEAD" and status != 304:
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_body_bytes:
                return failure(Outcome.FAILED, f"body of {declared} bytes exceeds the cap")
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > self.max_body_bytes:
                    return failure(
                        Outcome.FAILED, f"body exceeds the cap of {self.max_body_bytes} bytes"
                    )
            body = bytes(chunks)

        content_type = response.headers.get("content-type")
        if _is_challenge(content_type, body):
            return failure(Outcome.BLOCKED, f"challenge page (HTTP {status})")
        final_host = _host(str(response.url))
        if final_host != _host(requested_url) and _is_html(content_type):
            return failure(Outcome.BLOCKED, f"redirected to {final_host}")
        return Response(
            status=status,
            body=body,
            etag=response.headers.get("etag"),
            last_modified=_parse_http_date(response.headers.get("last-modified")),
            content_type=content_type,
            final_url=str(response.url),
        )


@dataclass(frozen=True, slots=True)
class _SavedFile:
    """A file the transport wrote to its destination."""

    status: int
    content_hash: str
    byte_size: int
    etag: str | None
    last_modified: datetime | None
    content_type: str | None
    final_url: str


class _FileSink:
    """A file being written: ``<destination>.part`` until :meth:`finish` renames it.

    Blocking file I/O; every method is called through ``asyncio.to_thread``.
    """

    def __init__(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._destination = destination
        self._part = destination.with_name(f"{destination.name}.part")
        self._file: BinaryIO = self._part.open("wb")
        self._hash = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        self._file.write(data)
        self._hash.update(data)
        self.size += len(data)

    def finish(self) -> str:
        """Close the file, move it to its destination and return its SHA-256."""
        self._file.close()
        self._part.replace(self._destination)
        return self._hash.hexdigest()

    def discard(self) -> None:
        """Close and delete the partial file (idempotent)."""
        self._file.close()
        self._part.unlink(missing_ok=True)


class _HttpSession:
    """Implements :class:`Session` over a client of its own (its own cookie jar)."""

    def __init__(
        self, client: httpx2.AsyncClient, transport: _Transport, delay: float, opened_at: datetime
    ) -> None:
        self._client = client
        self._transport = transport
        self._delay = delay
        self._opened_at = opened_at

    @property
    def opened_at(self) -> datetime:
        """When the session opened."""
        return self._opened_at

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        json: object | None = None,
    ) -> Response | FetchFailure:
        """One request of the session (see :class:`Session`)."""
        started = time.perf_counter()
        sent = dict(headers or {})
        content = None
        if json is not None:
            content = json_module.dumps(json).encode()
            if not any(name.lower() == "content-type" for name in sent):
                sent["Content-Type"] = "application/json"
        result = await self._transport.request(
            self._client,
            method,
            url,
            headers=sent,
            fetched_at=self._opened_at,
            delay=self._delay,
            params=params,
            content=content,
            retry=method in _REPEATABLE_METHODS,
        )
        logger.info(
            "http session request method=%s url=%s result=%s status=%s bytes=%s duration_ms=%d",
            method,
            loggable_url(url),
            result.outcome.value if isinstance(result, FetchFailure) else "ok",
            result.status,
            len(result.body) if isinstance(result, Response) else "-",
            (time.perf_counter() - started) * 1000,
        )
        return result


class HttpFetcher:
    """Implements :class:`SourceFetcher` (:class:`SessionFetcher` and :class:`FileFetcher`)
    over one shared ``httpx2.AsyncClient``.

    ``session_client`` builds the client of each :meth:`session`, so a session's cookies never
    reach the shared client; a fetcher built without it cannot open sessions.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        clock: Clock,
        *,
        session_client: Callable[[], httpx2.AsyncClient] | None = None,
        retry_pause_seconds: float = RETRY_PAUSE_SECONDS,
        max_body_bytes: int = MAX_BODY_BYTES,
        max_file_bytes: int = MAX_FILE_BYTES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._clock = clock
        self._session_client = session_client
        self._max_file_bytes = max_file_bytes
        self._transport = _Transport(
            retry_pause=retry_pause_seconds, max_body_bytes=max_body_bytes, sleep=sleep
        )

    async def download(
        self,
        url: str,
        *,
        destination: Path,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
    ) -> FileOutcome:
        """Stream ``url`` to ``destination`` with the change detection ``change_signal`` names.

        ``CONDITIONAL_GET``, ``LINK_DISCOVERY`` and ``API_METADATA`` send ``previous``'s
        validators (a 304 is unchanged); any other signal is a plain GET. A file hashing equal
        to ``previous`` is unchanged too, and deleted. The crawl delay is waited before every
        request; a transport error is retried once, from the start. An HTML answer is read
        into memory and classified like :meth:`fetch` (a challenge page is ``BLOCKED``), then
        written out when it is not refused. A failure leaves no file at ``destination``.
        """
        started = time.perf_counter()
        fetched_at = self._clock.now()
        conditional = change_signal in (
            ChangeSignal.CONDITIONAL_GET,
            ChangeSignal.LINK_DISCOVERY,
            ChangeSignal.API_METADATA,
        )
        headers = _conditional_headers(previous) if conditional else {}
        answer = await self._transport.download(
            self._client,
            url,
            headers=headers,
            fetched_at=fetched_at,
            delay=crawl_delay.total_seconds(),
            destination=destination,
            max_file_bytes=self._max_file_bytes,
        )
        result = await self._file_outcome(answer, headers, previous, destination, fetched_at)
        logger.info(
            "http download url=%s signal=%s result=%s status=%s bytes=%s duration_ms=%d",
            loggable_url(url),
            change_signal.value,
            _describe(result),
            result.status,
            result.byte_size if isinstance(result, FetchedFile) else "-",
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def _file_outcome(
        self,
        answer: _SavedFile | Response | FetchFailure,
        headers: Mapping[str, str],
        previous: SourceSnapshot | None,
        destination: Path,
        fetched_at: datetime,
    ) -> FileOutcome:
        if isinstance(answer, FetchFailure):
            return answer
        if isinstance(answer, Response):
            if answer.status == 304:
                return _not_modified_304(answer, headers, fetched_at)
            await asyncio.to_thread(_write_file, destination, answer.body)
            answer = _SavedFile(
                status=answer.status,
                content_hash=hashlib.sha256(answer.body).hexdigest(),
                byte_size=len(answer.body),
                etag=answer.etag,
                last_modified=answer.last_modified,
                content_type=answer.content_type,
                final_url=answer.final_url,
            )
        if previous is not None and previous.content_hash == answer.content_hash:
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            return NotModified(
                reason="content hash unchanged",
                etag=answer.etag,
                last_modified=answer.last_modified,
                fetched_at=fetched_at,
                status=answer.status,
            )
        return FetchedFile(
            path=destination,
            content_hash=answer.content_hash,
            etag=answer.etag,
            last_modified=answer.last_modified,
            content_type=answer.content_type,
            final_url=answer.final_url,
            byte_size=answer.byte_size,
            fetched_at=fetched_at,
            status=answer.status,
        )

    @asynccontextmanager
    async def session(self, *, crawl_delay: timedelta) -> AsyncIterator[Session]:
        """A session on a new client from ``session_client``, closed when the context ends.

        Raises:
            TypeError: The fetcher was built without a ``session_client``.
        """
        if self._session_client is None:
            raise TypeError("this HttpFetcher was built without a session_client")
        async with self._session_client() as client:
            yield _HttpSession(
                client, self._transport, crawl_delay.total_seconds(), self._clock.now()
            )

    async def fetch(
        self,
        url: str,
        *,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
        entry_url: str | None = None,
        profile: str | None = None,
        webdriver_disabled: bool = False,
        challenge_timeout: timedelta | None = None,
    ) -> FetchOutcome:
        """Fetch ``url`` with the change detection ``change_signal`` names.

        - ``CONDITIONAL_GET``: ``If-None-Match``/``If-Modified-Since`` from ``previous``; a 304
          is unchanged. ``LINK_DISCOVERY`` and ``API_METADATA`` fetch the URL their
          connector resolved the same way.
        - ``CONTENT_HASH``: a plain GET; a body hashing equal to ``previous`` is unchanged.
        - ``HEADER_COMPARE``: HEAD first; equal ETag/Last-Modified is unchanged, otherwise GET.

        After any GET, a body hashing equal to ``previous`` is reported as unchanged too, for
        servers that ignore conditional requests. ``crawl_delay`` is waited before every
        request (HEAD, GET and the retry). ``entry_url``, ``profile``, ``webdriver_disabled`` and
        ``challenge_timeout`` are accepted for the protocol and ignored: plain HTTP keeps no
        session to warm up and runs no browser, and the base never passes them here.
        """
        started = time.perf_counter()
        fetched_at = self._clock.now()
        delay = crawl_delay.total_seconds()
        match change_signal:
            case (
                ChangeSignal.CONDITIONAL_GET
                | ChangeSignal.LINK_DISCOVERY
                | ChangeSignal.API_METADATA
            ):
                result = await self._get(url, previous, fetched_at, delay, conditional=True)
            case ChangeSignal.CONTENT_HASH:
                result = await self._get(url, previous, fetched_at, delay, conditional=False)
            case ChangeSignal.HEADER_COMPARE:
                result = await self._header_compare(url, previous, fetched_at, delay)
        logger.info(
            "http fetch url=%s signal=%s result=%s status=%s bytes=%s duration_ms=%d",
            loggable_url(url),
            change_signal.value,
            _describe(result),
            result.status,
            result.byte_size if isinstance(result, FetchedDocument) else "-",
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def _header_compare(
        self, url: str, previous: SourceSnapshot | None, fetched_at: datetime, delay: float
    ) -> FetchOutcome:
        head = await self._request("HEAD", url, {}, fetched_at, delay)
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
        return await self._get(url, previous, fetched_at, delay, conditional=False)

    async def _get(
        self,
        url: str,
        previous: SourceSnapshot | None,
        fetched_at: datetime,
        delay: float,
        *,
        conditional: bool,
    ) -> FetchOutcome:
        headers = _conditional_headers(previous) if conditional else {}
        response = await self._request("GET", url, headers, fetched_at, delay)
        if isinstance(response, FetchFailure):
            return response
        if response.status == 304:
            return _not_modified_304(response, headers, fetched_at)
        document = document_from(response, fetched_at)
        if previous is not None and previous.content_hash == document.content_hash:
            return NotModified(
                reason="content hash unchanged",
                etag=response.etag,
                last_modified=response.last_modified,
                fetched_at=fetched_at,
                status=response.status,
            )
        return document

    async def _request(
        self, method: str, url: str, headers: dict[str, str], fetched_at: datetime, delay: float
    ) -> Response | FetchFailure:
        """One request on the shared client, retried once on a transport error."""
        return await self._transport.request(
            self._client, method, url, headers=headers, fetched_at=fetched_at, delay=delay
        )


def loggable_url(url: str) -> str:
    """``url`` as a log line shows it: without its query string or fragment, which may carry
    a search's names or an identifier (the NPPES API), never needed to tell requests apart."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _conditional_headers(previous: SourceSnapshot | None) -> dict[str, str]:
    """``If-None-Match``/``If-Modified-Since`` from the previous snapshot's validators."""
    headers: dict[str, str] = {}
    if previous is not None:
        if previous.etag:
            headers["If-None-Match"] = previous.etag
        if previous.last_modified:
            headers["If-Modified-Since"] = _http_date(previous.last_modified)
    return headers


def _not_modified_304(
    response: Response, headers: Mapping[str, str], fetched_at: datetime
) -> NotModified | FetchFailure:
    """A 304 is unchanged only as the answer to a conditional request."""
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


def _refusal(response: httpx2.Response, fetched_at: datetime) -> FetchFailure | None:
    """The failure an answer's status and headers alone make it, or ``None`` when it may be
    read: a challenge header or 401/403/429 is ``BLOCKED``, 5xx ``SOURCE_UNAVAILABLE``, any
    other status but 2xx and 304 ``FAILED``."""
    status = response.status_code
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return _failure(response, fetched_at, f"challenge page (HTTP {status})", Outcome.BLOCKED)
    if status in _BLOCKING_STATUSES:
        retry_after = response.headers.get("retry-after")
        suffix = f", Retry-After {retry_after}" if retry_after else ""
        return _failure(response, fetched_at, f"HTTP {status}{suffix}", Outcome.BLOCKED)
    if status >= 500:
        return _failure(response, fetched_at, f"HTTP {status}", Outcome.SOURCE_UNAVAILABLE)
    if status != 304 and not 200 <= status < 300:
        return _failure(response, fetched_at, f"unexpected HTTP {status}")
    return None


def _failure(
    response: httpx2.Response,
    fetched_at: datetime,
    note: str,
    outcome: Outcome = Outcome.FAILED,
) -> FetchFailure:
    return FetchFailure(
        outcome=outcome, status=response.status_code, note=note, fetched_at=fetched_at
    )


def _write_file(destination: Path, body: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)


def _is_html(content_type: str | None) -> bool:
    return content_type is not None and "html" in content_type.lower()


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_challenge(content_type: str | None, body: bytes) -> bool:
    """Whether an HTML body is a bot-manager challenge or CAPTCHA page rather than data."""
    if not _is_html(content_type):
        return False
    head = body[:_CHALLENGE_SCAN_BYTES].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _same_validators(previous: SourceSnapshot, response: Response) -> bool:
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


def _describe(result: FetchOutcome | FileOutcome) -> str:
    match result:
        case FetchedDocument() | FetchedFile():
            return "new"
        case NotModified():
            return "unchanged"
        case FetchFailure():
            return result.outcome.value
