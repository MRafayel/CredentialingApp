"""The base class every bulk-list connector extends: fetch, snapshot, parse, instrument.

A subclass declares its ``capabilities`` and ``download_url`` and implements ``parse``;
nothing else is source-specific.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import ClassVar

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, Fetcher
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.framework.snapshots import snapshot_from
from credentialing.domain.enums import Outcome
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    FetchResult,
    SourceSnapshot,
)

logger = logging.getLogger(__name__)


class BulkListConnectorBase(ABC):
    """Implements :class:`~credentialing.domain.ports.BulkListConnector` for downloaded lists.

    ``fetch`` downloads through the shared fetcher, builds the snapshot and, only when the
    source sent a new document, parses it off the event loop. A parse error of any kind turns
    the snapshot into ``FAILED`` with no records, so a broken file never replaces the list.
    """

    download_url: ClassVar[str]
    """The URL of the file itself; ``capabilities.source_url`` is the page people read."""

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    @property
    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""

    @abstractmethod
    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map the document into records belonging to ``snapshot``."""

    async def fetch(self, previous: SourceSnapshot | None) -> FetchResult:
        """Fetch the list, using ``previous`` to skip the download when nothing changed."""
        started = time.perf_counter()
        source_id = self.capabilities.source_id
        result = await self._fetcher.fetch(
            self.download_url, previous=previous, change_signal=self.capabilities.change_signal
        )
        snapshot = snapshot_from(
            result, source_id=source_id, url=self.download_url, previous=previous
        )
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
