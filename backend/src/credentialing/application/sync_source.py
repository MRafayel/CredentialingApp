"""Use case: fetch one public source, store the snapshot and update its current records.

A bulk list replaces the source's exclusion records; a dataset (NPPES) is loaded into the
NPI mirror by difference, and the load's counts, phase timings and CPU time are stored on the
snapshot. A sync that changed rows then relinks exclusion records to NPIs (the list's own
records after a list sync, every record after a mirror load) and rechecks the monitored
rosters (against that list after a list sync, the NPI mirror after a mirror load); a linking
or recheck failure is reported, never undoes the sync.
"""

import logging
import math
import time
from datetime import datetime, timedelta
from typing import Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import UnknownSourceError
from credentialing.application.link_npis import (
    NPPES_LOAD_TRIGGER,
    NpiLinking,
    list_sync_trigger,
)
from credentialing.application.measure import cpu_seconds
from credentialing.application.recheck_providers import ProviderRechecking
from credentialing.domain.enums import DatasetLoadMode, Outcome
from credentialing.domain.errors import DatasetShrankError
from credentialing.domain.models import (
    DatasetLoad,
    ExclusionRecord,
    MonitoringRun,
    NpiLinkRun,
    SnapshotDiff,
    SourceSnapshot,
)
from credentialing.domain.policies import poll_anchor
from credentialing.domain.ports import (
    BulkListConnector,
    Clock,
    ConnectorDirectory,
    DatasetConnector,
    ExclusionRecordRepository,
    NpiRegistryRepository,
    SnapshotRepository,
)
from credentialing.domain.types import SourceId

logger = logging.getLogger(__name__)

SHRINK_GUARD_RATIO: Final = 0.5
"""A new list with fewer rows than this share of the last applied one is not applied."""

SHRINK_GUARD_MIN_ROWS: Final = 100
"""Lists smaller than this are too small for the ratio to mean anything; no guard."""

DATASET_SHRINK_GUARD_MIN_ROWS: Final = 100_000
"""Full dataset files whose last load had fewer rows than this get no shrink guard."""

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
    """What one sync did: the stored snapshot and, when a new list was loaded, its diff, or,
    when a new dataset file was loaded, what the load did.

    ``linking`` is the NPI linking run the sync triggered (rows changed); ``linking_failed``
    says, by error class, why that run failed. ``recheck`` is the roster recheck the sync
    triggered after it, ``recheck_failed`` why that recheck failed. The sync itself stands
    either way.
    """

    model_config = ConfigDict(frozen=True)

    snapshot: SourceSnapshot
    diff: SnapshotDiff | None = None
    load: DatasetLoad | None = None
    linking: NpiLinkRun | None = None
    linking_failed: str | None = None
    recheck: MonitoringRun | None = None
    recheck_failed: str | None = None


class SyncSource:
    """Polls a bulk-list or dataset source and keeps its records in step with it.

    ``npi_registry`` is the mirror dataset sources load into; a sync of a dataset source
    without it is a wiring error (``TypeError``). ``link_npis`` relinks exclusion records to
    NPIs after a sync that changed rows, and ``recheck`` then rechecks the monitored rosters;
    without them neither runs.
    """

    def __init__(
        self,
        connectors: ConnectorDirectory,
        snapshots: SnapshotRepository,
        records: ExclusionRecordRepository,
        clock: Clock,
        *,
        npi_registry: NpiRegistryRepository | None = None,
        link_npis: NpiLinking | None = None,
        recheck: ProviderRechecking | None = None,
    ) -> None:
        self._connectors = connectors
        self._snapshots = snapshots
        self._records = records
        self._clock = clock
        self._npi_registry = npi_registry
        self._link_npis = link_npis
        self._recheck = recheck

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

        A dataset source is loaded as :meth:`_run_dataset` describes.

        A new list that added, changed, re-added or rewritten rows relinks that list's records
        to NPIs (``list_sync:<source_id>``), see :meth:`_relink`, then rechecks every roster
        provider against that list, see :meth:`_rechecked`.

        Raises:
            UnknownSourceError: No bulk-list or dataset connector is registered as
                ``source_id``.
        """
        connector = self._connectors.get(source_id)
        if isinstance(connector, DatasetConnector):
            return await self._run_dataset(connector, force=force)
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
        report = SyncReport(snapshot=snapshot, diff=diff)
        if diff.added + diff.changed + diff.readded + diff.rewritten:
            trigger = list_sync_trigger(source_id)
            report = await self._relink(report, source_id, trigger)
            return await self._rechecked(report, trigger, source_id=source_id)
        return report

    async def _run_dataset(self, connector: DatasetConnector, *, force: bool) -> SyncReport:
        """Fetch a dataset source, store the snapshot and load a new file into the mirror.

        The snapshot is stored before the load (the mirror's rows refer to it); the load's
        counts, phase timings (the fetch as ``download``, then the loader's phases) and the
        process's CPU time over fetch and load are stored on it afterwards, and the file is
        closed (deleted) whatever happens.

        A full file with fewer than half the distinct NPIs of the last full file loaded (when
        that one had at least :data:`DATASET_SHRINK_GUARD_MIN_ROWS`) is not applied, the
        same guard as for lists: the loader refuses before writing, and a ``FAILED``
        snapshot with the counts follows, so the next poll downloads the file again.
        ``force`` applies it anyway. A load that fails is followed by a ``FAILED`` snapshot
        too, and re-raised.

        A load that added, changed, deactivated or flagged rows relinks every exclusion record
        (``nppes_load``): the mirror changed under every link. It then reverifies the NPI of
        every roster provider that has one against the mirror: a full file shows a
        deactivation as a changed row, a deactivation file as a deactivated one, and a new row
        can answer an NPI the mirror lacked.
        """
        registry = self._npi_registry
        if registry is None:
            raise TypeError("SyncSource needs an npi_registry to load dataset sources")
        source_id = connector.capabilities.source_id
        previous = None if force else await self._snapshots.latest(source_id)
        cpu_started, started = cpu_seconds(), time.perf_counter()
        fetched = await connector.fetch_dataset(previous)
        download = timedelta(seconds=time.perf_counter() - started)
        snapshot, dataset = fetched.snapshot, fetched.dataset
        if dataset is None:
            await self._snapshots.add(snapshot)
            return SyncReport(snapshot=snapshot)
        try:
            await self._snapshots.add(snapshot)
            before = None if force else await self._last_full_rows(source_id, fetched.mode)
            try:
                load = await registry.load(
                    dataset,
                    mode=fetched.mode,
                    snapshot_id=snapshot.id,
                    now=self._clock.now(),
                    min_rows=math.ceil(before * SHRINK_GUARD_RATIO) if before else None,
                )
            except DatasetShrankError as error:
                logger.warning(
                    "row count drop source_id=%s before=%s after=%d", source_id, before, error.rows
                )
                refused = self._failed_after(
                    snapshot,
                    f"row count dropped from {before} to {error.rows}; "
                    "refusing to apply without --force",
                )
                await self._snapshots.add(refused)
                return SyncReport(snapshot=refused)
            except Exception:
                logger.exception("loading the dataset failed source_id=%s", source_id)
                await self._snapshots.add(self._storage_failed(snapshot))
                raise
        finally:
            dataset.close()
        load = load.model_copy(
            update={
                "phases": {"download": download, **load.phases},
                "cpu_time": timedelta(seconds=cpu_seconds() - cpu_started),
            }
        )
        await self._snapshots.set_load(snapshot.id, load)
        stored = snapshot.model_copy(update={"load": load, "row_count": load.scanned})
        report = SyncReport(snapshot=stored, load=load)
        if load.added + load.changed + load.deactivated + load.missing:
            report = await self._relink(report, None, NPPES_LOAD_TRIGGER)
            return await self._rechecked(report, NPPES_LOAD_TRIGGER, npi_only=True)
        return report

    async def _relink(
        self, report: SyncReport, source_id: SourceId | None, trigger: str
    ) -> SyncReport:
        """Run the NPI linking a changed sync triggers and add it to the report.

        A failure is logged and reported by error class (``linking_failed``); the sync's
        snapshot and records stay as stored, and the next triggered run relinks what is stale.
        """
        if self._link_npis is None:
            return report
        try:
            run = await self._link_npis.run(source_id=source_id, trigger=trigger)
        except Exception as error:
            logger.exception("npi linking failed trigger=%s", trigger)
            failed = f"NPI linking failed ({type(error).__name__})"
            return report.model_copy(update={"linking_failed": failed})
        return report.model_copy(update={"linking": run})

    async def _rechecked(
        self,
        report: SyncReport,
        trigger: str,
        *,
        source_id: SourceId | None = None,
        npi_only: bool = False,
    ) -> SyncReport:
        """Run the roster recheck a changed sync triggers and add it to the report.

        A failure of the whole recheck is logged and reported by error class
        (``recheck_failed``); the sync stands, and the next scheduled recheck covers what this
        one missed.
        """
        if self._recheck is None:
            return report
        try:
            run = await self._recheck.run(trigger=trigger, source_id=source_id, npi_only=npi_only)
        except Exception as error:
            # By error class only: a recheck touches roster data, which is never logged.
            logger.error("roster recheck failed trigger=%s error=%s", trigger, type(error).__name__)
            failed = f"roster recheck failed ({type(error).__name__})"
            return report.model_copy(update={"recheck_failed": failed})
        return report.model_copy(update={"recheck": run})

    async def _last_full_rows(self, source_id: SourceId, mode: DatasetLoadMode) -> int | None:
        """The distinct NPIs of the source's last full load, when the guard applies to it."""
        if mode is not DatasetLoadMode.FULL:
            return None
        loaded = await self._snapshots.latest_loaded(source_id)
        last = loaded.load if loaded else None
        if last is None or last.mode is not DatasetLoadMode.FULL:
            return None
        rows = last.scanned - last.duplicates
        return rows if rows >= DATASET_SHRINK_GUARD_MIN_ROWS else None

    async def next_due(self, source_id: SourceId) -> datetime | None:
        """When ``source_id`` is next due: its latest completed fetch plus its poll interval,
        less :data:`DUE_TOLERANCE`. ``None`` when it never completed, so it is due now.

        A completed fetch (new or unchanged) counts, and so does a later ``BLOCKED`` one
        (``domain.policies.poll_anchor``): a refusing firewall is asked again one interval
        later, politely. After ``FAILED`` or ``SOURCE_UNAVAILABLE`` the source stays due on
        every run until a fetch completes.

        Raises:
            UnknownSourceError: No bulk-list or dataset connector is registered as
                ``source_id``.
        """
        connector = self._connectors.get(source_id)
        if not isinstance(connector, BulkListConnector | DatasetConnector):
            raise UnknownSourceError(source_id)
        anchor = poll_anchor(
            await self._snapshots.latest(source_id),
            await self._snapshots.latest_completed(source_id),
        )
        if anchor is None:
            return None
        return anchor.fetched_at + connector.capabilities.poll_interval - DUE_TOLERANCE

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
        return self._failed_after(
            snapshot, f"records of snapshot {snapshot.id} could not be stored"
        )

    def _failed_after(self, snapshot: SourceSnapshot, note: str) -> SourceSnapshot:
        """A ``FAILED`` snapshot just after ``snapshot``, so the next poll trusts none of its
        validators and downloads again."""
        return SourceSnapshot(
            id=uuid4(),
            source_id=snapshot.source_id,
            fetched_at=max(self._clock.now(), snapshot.fetched_at + timedelta(microseconds=1)),
            outcome=Outcome.FAILED,
            source_url=snapshot.source_url,
            note=note,
        )
