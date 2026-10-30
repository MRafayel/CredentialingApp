"""Use case: fetch one public source, store the snapshot and update its current records."""

import logging
from datetime import datetime, timedelta
from typing import Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import UnknownSourceError
from credentialing.domain.enums import Outcome
from credentialing.domain.models import ExclusionRecord, SnapshotDiff, SourceSnapshot
from credentialing.domain.ports import (
    BulkListConnector,
    Clock,
    ConnectorDirectory,
    ExclusionRecordRepository,
    SnapshotRepository,
)
from credentialing.domain.types import SourceId

logger = logging.getLogger(__name__)

SHRINK_GUARD_RATIO: Final = 0.5
"""A new list with fewer rows than this share of the last applied one is not applied."""

SHRINK_GUARD_MIN_ROWS: Final = 100
"""Lists smaller than this are too small for the ratio to mean anything; no guard."""

DUE_TOLERANCE: Final = timedelta(hours=1)
"""How early a source counts as due, so a daily cron job is not skipped because yesterday's
run finished a few seconds after today's started."""


def source_changed(applied: SourceSnapshot | None, snapshot: SourceSnapshot) -> bool:
    """Whether ``snapshot``'s bytes differ from the last applied (completed) snapshot's.

    Only a change in the source can remove a row from its published list; the same bytes
    parsed again (a parser fix under ``--force``) cannot. Without a hash on either side the
    sameness is unproven, so the source counts as changed and a missing row as removed.
    """
    return (
        applied is None
        or applied.content_hash is None
        or snapshot.content_hash is None
        or applied.content_hash != snapshot.content_hash
    )


class SyncReport(BaseModel):
    """What one sync did: the stored snapshot and, when a new list was loaded, its diff."""

    model_config = ConfigDict(frozen=True)

    snapshot: SourceSnapshot
    diff: SnapshotDiff | None = None


class SyncSource:
    """Polls a bulk-list source and keeps its current records in step with it."""

    def __init__(
        self,
        connectors: ConnectorDirectory,
        snapshots: SnapshotRepository,
        records: ExclusionRecordRepository,
        clock: Clock,
    ) -> None:
        self._connectors = connectors
        self._snapshots = snapshots
        self._records = records
        self._clock = clock

    async def run(self, source_id: SourceId, *, force: bool = False) -> SyncReport:
        """Fetch ``source_id`` once, store the snapshot, and load the records if new.

        The latest snapshot (whatever its outcome) is handed to the connector so an unchanged
        source costs one request. ``force`` ignores it and downloads and parses again, which
        is how a parser fix reaches a source that has not changed.

        A new list with less than half the rows of the last applied one (when that one had at
        least 100) is not applied: a sudden drop is far more often a broken export than mass
        reinstatement, and applying it would close every missing row. The snapshot is stored
        as ``FAILED`` with the counts in its note; ``force`` applies it anyway.

        Rows missing from the new list count as removed from the published list only when the
        source's bytes changed since the last applied snapshot (:func:`source_changed`); a
        forced re-parse of the same bytes only supersedes them.

        If storing the records fails after the snapshot was stored, a ``FAILED`` snapshot is
        added after it, so the next poll does not trust the stored validators and downloads
        the list again.

        Raises:
            UnknownSourceError: No bulk-list connector is registered as ``source_id``.
        """
        connector = self._connectors.get(source_id)
        if not isinstance(connector, BulkListConnector):
            raise UnknownSourceError(source_id)
        previous = None if force else await self._snapshots.latest(source_id)
        result = await connector.fetch(previous)
        snapshot, records = result.snapshot, result.records
        applied = await self._snapshots.latest_completed(source_id)
        if records is not None and not force:
            snapshot, records = await self._guard_shrink(snapshot, records, applied)
        await self._snapshots.add(snapshot)
        if records is None:
            return SyncReport(snapshot=snapshot)
        try:
            diff = await self._records.replace_current(
                source_id,
                snapshot.id,
                records,
                self._clock.now(),
                source_changed=source_changed(applied, snapshot),
            )
        except Exception:
            logger.exception("storing records failed source_id=%s", source_id)
            await self._snapshots.add(self._storage_failed(snapshot))
            raise
        return SyncReport(snapshot=snapshot, diff=diff)

    async def next_due(self, source_id: SourceId) -> datetime | None:
        """When ``source_id`` is next due: its latest completed fetch plus its poll interval,
        less :data:`DUE_TOLERANCE`. ``None`` when it never completed, so it is due now.

        Only a completed fetch (new or unchanged) counts: after a failure the source stays due
        on every run until a fetch completes.

        Raises:
            UnknownSourceError: No bulk-list connector is registered as ``source_id``.
        """
        connector = self._connectors.get(source_id)
        if not isinstance(connector, BulkListConnector):
            raise UnknownSourceError(source_id)
        completed = await self._snapshots.latest_completed(source_id)
        if completed is None:
            return None
        return completed.fetched_at + connector.capabilities.poll_interval - DUE_TOLERANCE

    async def _guard_shrink(
        self,
        snapshot: SourceSnapshot,
        records: tuple[ExclusionRecord, ...],
        applied: SourceSnapshot | None,
    ) -> tuple[SourceSnapshot, tuple[ExclusionRecord, ...] | None]:
        before = applied.row_count if applied is not None else None
        if (
            before is None
            or before < SHRINK_GUARD_MIN_ROWS
            or len(records) >= before * SHRINK_GUARD_RATIO
        ):
            return snapshot, records
        logger.warning(
            "row count drop source_id=%s before=%d after=%d",
            snapshot.source_id,
            before,
            len(records),
        )
        refused = snapshot.model_copy(
            update={
                "outcome": Outcome.FAILED,
                "content_hash": None,
                "etag": None,
                "last_modified": None,
                "note": (
                    f"row count dropped from {before} to {len(records)}; "
                    "refusing to apply without --force"
                ),
            }
        )
        return refused, None

    def _storage_failed(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        return SourceSnapshot(
            id=uuid4(),
            source_id=snapshot.source_id,
            fetched_at=max(self._clock.now(), snapshot.fetched_at + timedelta(microseconds=1)),
            outcome=Outcome.FAILED,
            source_url=snapshot.source_url,
            note=f"records of snapshot {snapshot.id} could not be stored",
        )
