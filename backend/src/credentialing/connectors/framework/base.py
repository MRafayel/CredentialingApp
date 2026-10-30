"""The base class every bulk-list connector extends: fetch, snapshot, parse, instrument.

A subclass declares its ``capabilities`` and implements ``parse``, plus where its file is:

- a stable file: ``download_url``;
- a file whose URL changes on every release (``LINK_DISCOVERY``): ``landing_url`` and
  :meth:`~BulkListConnectorBase.find_link`, which picks the file's link out of the landing
  page;
- a file described by an API (``API_METADATA``): ``metadata_url`` and
  :meth:`~BulkListConnectorBase.read_metadata`, which reads the file's URL and MD5 hash.

Nothing else is source-specific. Either way an unchanged source costs one request per poll.
"""

import asyncio
import hashlib
import html
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final, TypeGuard
from urllib.parse import urljoin, urlsplit

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import (
    FetchedDocument,
    Fetcher,
    FetchFailure,
    FetchOutcome,
    NotModified,
)
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.framework.snapshots import snapshot_from
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    FetchResult,
    SourceSnapshot,
)

logger = logging.getLogger(__name__)

_HREF: Final = re.compile(r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)


def first_href(page: str, pattern: re.Pattern[str]) -> str:
    """The first link target on an HTML page that ``pattern`` finds, entities unescaped.

    For :meth:`BulkListConnectorBase.find_link`: a source's file link is recognized by its
    path ("/wp-content/uploads/sanction-list-2026-09-30.xlsx"), not by its link text.

    Raises:
        ParseError: No link on the page matches.
    """
    for found in _HREF.finditer(page):
        target = html.unescape(found[1] if found[1] is not None else found[2])
        if pattern.search(target):
            return target
    raise ParseError(f"no link matching {pattern.pattern}")


@dataclass(frozen=True, slots=True)
class Metadata:
    """What a source's API says about its current file: where it is, its MD5, when it changed."""

    url: str
    hash: str
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class Located:
    """The file to download on this poll, as :meth:`BulkListConnectorBase.locate` or
    :meth:`BulkListConnectorBase.metadata` resolved it.

    ``url`` becomes the snapshot's ``source_url``, so the next poll can tell an unchanged link.
    ``md5`` (an API's hash of the file) must equal the downloaded body's MD5 and is kept as the
    snapshot's ``etag``, the validator the next poll compares; ``last_modified`` is the API's.
    """

    url: str
    md5: str | None = None
    last_modified: datetime | None = None


type Location = Located | NotModified | FetchFailure


class BulkListConnectorBase(ABC):
    """Implements :class:`~credentialing.domain.ports.BulkListConnector` for downloaded lists.

    ``fetch`` resolves the file's URL when the source needs it, downloads through the shared
    fetcher, builds the snapshot and, only when the source sent a new document, parses it off
    the event loop. A parse error of any kind turns the snapshot into ``FAILED`` with no
    records, so a broken file never replaces the list.
    """

    download_url: ClassVar[str]
    """The URL of the file itself; ``capabilities.source_url`` is the page people read.
    Not declared by link-discovery and API-metadata sources, whose file URL changes."""

    landing_url: ClassVar[str | None] = None
    """``LINK_DISCOVERY``: the page that links the current file."""

    metadata_url: ClassVar[str | None] = None
    """``API_METADATA``: the API request describing the current file."""

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    @property
    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""

    @abstractmethod
    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map the document into records belonging to ``snapshot``."""

    def find_link(self, html: str) -> str:
        """``LINK_DISCOVERY``: the current file's link on the landing page (may be relative).

        Raises:
            ParseError: The page has no such link.
        """
        raise NotImplementedError(f"{type(self).__name__} does not discover links")

    def read_metadata(self, body: bytes) -> Metadata:
        """``API_METADATA``: the file's URL, MD5 hash and modified time from the API's answer.

        Raises:
            ParseError: The answer does not have the expected shape.
        """
        raise NotImplementedError(f"{type(self).__name__} reads no API metadata")

    async def locate(self, previous: SourceSnapshot | None) -> Location:
        """``LINK_DISCOVERY``: read the landing page and find the file to download.

        The landing page is fetched with a plain GET: the snapshot's validators belong to the
        file, not the page. When the link found equals ``previous.source_url`` and that fetch
        completed, the file is unchanged and this one request is the whole poll.
        """
        url = _required(self.landing_url, "landing_url", self)
        page = await self._get(url, previous=None, change_signal=ChangeSignal.CONTENT_HASH)
        if not isinstance(page, FetchedDocument):
            return _unchanged(page, previous, "landing page unchanged")
        try:
            link = self.find_link(_decode(page))
        except Exception as error:  # any extraction failure is this source's FAILED outcome
            return _failure(page, "no download link on the landing page", error)
        found = urljoin(page.final_url, link.strip())
        if urlsplit(found).scheme not in ("https", "http"):
            return _failure(page, "the download link is not a web address")
        if _completed(previous) and previous.source_url == found:
            return _not_modified(page, "link unchanged")
        return Located(url=found)

    async def metadata(self, previous: SourceSnapshot | None) -> Location:
        """``API_METADATA``: ask the API for the file's URL and hash.

        When the hash equals the one kept on ``previous`` (its ``etag``) and that fetch
        completed, the file is unchanged and this one request is the whole poll.
        """
        url = _required(self.metadata_url, "metadata_url", self)
        answer = await self._get(url, previous=None, change_signal=ChangeSignal.CONTENT_HASH)
        if not isinstance(answer, FetchedDocument):
            return _unchanged(answer, previous, "metadata unchanged")
        try:
            described = self.read_metadata(answer.body)
        except Exception as error:  # any unreadable answer is this source's FAILED outcome
            return _failure(answer, "metadata unreadable", error)
        md5 = described.hash.strip().lower()
        if _completed(previous) and previous.etag == md5:
            return NotModified(
                reason="metadata unchanged",
                etag=md5,
                last_modified=described.last_modified,
                fetched_at=answer.fetched_at,
                status=answer.status,
            )
        return Located(
            url=urljoin(url, described.url), md5=md5, last_modified=described.last_modified
        )

    async def fetch(self, previous: SourceSnapshot | None) -> FetchResult:
        """Fetch the list, using ``previous`` to skip the download when nothing changed."""
        started = time.perf_counter()
        capabilities = self.capabilities
        source_id = capabilities.source_id
        match capabilities.change_signal:
            case ChangeSignal.LINK_DISCOVERY:
                location = await self.locate(previous)
                fallback_url = _required(self.landing_url, "landing_url", self)
            case ChangeSignal.API_METADATA:
                location = await self.metadata(previous)
                fallback_url = _required(self.metadata_url, "metadata_url", self)
            case _:
                location = Located(url=self.download_url)
                fallback_url = self.download_url
        if isinstance(location, Located):
            result = await self._download(location, previous)
            url = location.url
        else:
            result = location
            unchanged = isinstance(result, NotModified) and previous is not None
            url = previous.source_url if unchanged and previous else fallback_url
        snapshot = snapshot_from(result, source_id=source_id, url=url, previous=previous)
        if isinstance(location, Located) and snapshot.outcome is Outcome.COMPLETED:
            snapshot = _stamp(snapshot, location, resolved=url != fallback_url)
        records: tuple[ExclusionRecord, ...] | None = None
        if isinstance(result, FetchedDocument):
            snapshot, records = await asyncio.to_thread(self._parse, result.body, snapshot)
        logger.info(
            "source fetch source_id=%s outcome=%s status=%s bytes=%s rows=%s note=%r "
            "duration_ms=%d",
            source_id,
            snapshot.outcome.value,
            result.status,
            snapshot.byte_size,
            snapshot.row_count,
            snapshot.note,
            (time.perf_counter() - started) * 1000,
        )
        return FetchResult(snapshot=snapshot, records=records)

    async def _get(
        self, url: str, *, previous: SourceSnapshot | None, change_signal: ChangeSignal
    ) -> FetchOutcome:
        return await self._fetcher.fetch(
            url,
            previous=previous,
            change_signal=change_signal,
            crawl_delay=self.capabilities.crawl_delay,
        )

    async def _download(self, location: Located, previous: SourceSnapshot | None) -> FetchOutcome:
        """Download the located file; conditional only when it is the previous fetch's URL."""
        signal = self.capabilities.change_signal
        if signal in (ChangeSignal.LINK_DISCOVERY, ChangeSignal.API_METADATA):
            same = previous is not None and previous.source_url == location.url
            signal = ChangeSignal.CONDITIONAL_GET if same else ChangeSignal.CONTENT_HASH
        result = await self._get(location.url, previous=previous, change_signal=signal)
        if (
            location.md5 is not None
            and isinstance(result, FetchedDocument)
            and hashlib.md5(result.body, usedforsecurity=False).hexdigest() != location.md5
        ):
            return _failure(result, "hash mismatch: the file is not the one the API describes")
        return result

    def _parse(
        self, body: bytes, snapshot: SourceSnapshot
    ) -> tuple[SourceSnapshot, tuple[ExclusionRecord, ...] | None]:
        try:
            parsed = self.parse(body, snapshot)
        except Exception as error:  # any parser failure is this source's FAILED outcome
            reason = str(error) if isinstance(error, ParseError) else type(error).__name__
            failed = snapshot.model_copy(
                update={
                    "outcome": Outcome.FAILED,
                    "content_hash": None,
                    "etag": None,
                    "last_modified": None,
                    "note": f"parse failed: {reason}",
                }
            )
            return failed, None
        updated = snapshot.model_copy(
            update={"row_count": len(parsed.records), "note": parsed.note()}
        )
        return updated, parsed.records


def _required(value: str | None, name: str, connector: BulkListConnectorBase) -> str:
    if value is None:
        raise TypeError(f"{type(connector).__name__} declares no {name}")
    return value


def _completed(previous: SourceSnapshot | None) -> TypeGuard[SourceSnapshot]:
    return previous is not None and previous.outcome is Outcome.COMPLETED


def _decode(document: FetchedDocument) -> str:
    """An HTML page as text, in the charset its Content-Type names (UTF-8 otherwise)."""
    charset = "utf-8"
    for parameter in (document.content_type or "").split(";")[1:]:
        key, _, value = parameter.partition("=")
        if key.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip('"')
    try:
        return document.body.decode(charset, errors="replace")
    except LookupError:
        return document.body.decode("utf-8", errors="replace")


def _failure(document: FetchedDocument, note: str, error: Exception | None = None) -> FetchFailure:
    if error is not None:
        reason = str(error) if isinstance(error, ParseError) else type(error).__name__
        note = f"{note}: {reason}"
    return FetchFailure(
        outcome=Outcome.FAILED,
        status=document.status,
        note=note,
        fetched_at=document.fetched_at,
    )


def _not_modified(document: FetchedDocument, reason: str) -> NotModified:
    return NotModified(
        reason=reason,
        etag=None,
        last_modified=None,
        fetched_at=document.fetched_at,
        status=document.status,
    )


def _unchanged(
    result: NotModified | FetchFailure, previous: SourceSnapshot | None, reason: str
) -> NotModified | FetchFailure:
    """A failure as it is; a 304 to the landing or API request as unchanged, if it can be."""
    if isinstance(result, FetchFailure):
        return result
    if not _completed(previous):
        return FetchFailure(
            outcome=Outcome.FAILED,
            status=result.status,
            note="reported unchanged with no completed fetch to compare",
            fetched_at=result.fetched_at,
        )
    return NotModified(
        reason=reason,
        etag=None,
        last_modified=None,
        fetched_at=result.fetched_at,
        status=result.status,
    )


def _stamp(snapshot: SourceSnapshot, location: Located, *, resolved: bool) -> SourceSnapshot:
    """Keep the located URL (not the redirect target) and the API's validators."""
    update: dict[str, object] = {}
    if resolved:
        update["source_url"] = location.url
    if location.md5 is not None:
        update["etag"] = location.md5
    if location.last_modified is not None:
        update["last_modified"] = location.last_modified
    return snapshot.model_copy(update=update) if update else snapshot
