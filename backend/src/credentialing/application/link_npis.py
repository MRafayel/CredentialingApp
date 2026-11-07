"""Use case: link exclusion records to NPIs of the registry mirror.

Every latest exclusion record whose links are missing or were made against an older mirror
load is relinked: its candidates are discovered in the mirror in batches, scored by the
domain (:func:`~credentialing.domain.policies.link_record_to_npis`) and its links rewritten.
An inferred NPI is evidence for a person to weigh; it never becomes the record's published
NPI. Runs are triggered by a list sync that changed rows, by an NPPES load that changed rows,
and by the ``link-npis`` command; never on a schedule of their own. Each run is stored with
its counts, phase timings and CPU time.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from credentialing.application.measure import cpu_seconds, phase
from credentialing.domain.enums import MatchLevel
from credentialing.domain.models import NpiLink, NpiLinkRun, NpiLinkSummary, SourceSnapshot
from credentialing.domain.policies import link_record_to_npis
from credentialing.domain.ports import (
    Clock,
    ConnectorDirectory,
    LinkCandidates,
    NpiLinkRepository,
    SnapshotRepository,
)
from credentialing.domain.types import SourceId

logger = logging.getLogger(__name__)

BATCH_SIZE: Final = 2_000
"""Records per discovery query and per write transaction."""

CLI_TRIGGER: Final = "cli"
NPPES_LOAD_TRIGGER: Final = "nppes_load"

MIRROR_NOT_LOADED: Final = "the NPI mirror has never been loaded; nothing was linked"


def list_sync_trigger(source_id: SourceId) -> str:
    """The trigger of a run that follows a sync of ``source_id`` which changed rows."""
    return f"list_sync:{source_id}"


class NpiLinking(Protocol):
    """What a sync needs to relink after it changed rows (a :class:`LinkNpis`, or a fake)."""

    async def run(self, *, source_id: SourceId | None, trigger: str) -> NpiLinkRun:
        """Relink the stale records (of ``source_id`` only, when given)."""
        ...


async def latest_mirror_load(
    connectors: ConnectorDirectory, snapshots: SnapshotRepository
) -> SourceSnapshot | None:
    """The newest snapshot whose file was loaded into the mirror, across every dataset source
    (monthly, weekly, deactivations); None when the mirror was never loaded."""
    loads = [
        await snapshots.latest_loaded(connector.capabilities.source_id)
        for connector in connectors.dataset_connectors()
    ]
    loaded = [snapshot for snapshot in loads if snapshot is not None]
    return max(loaded, key=lambda snapshot: (snapshot.fetched_at, str(snapshot.id)), default=None)


class LinkNpis:
    """Relinks stale exclusion records against the newest mirror load, measured."""

    def __init__(
        self,
        connectors: ConnectorDirectory,
        snapshots: SnapshotRepository,
        links: NpiLinkRepository,
        clock: Clock,
        *,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._connectors = connectors
        self._snapshots = snapshots
        self._links = links
        self._clock = clock
        self._batch_size = batch_size

    async def run(self, *, source_id: SourceId | None, trigger: str) -> NpiLinkRun:
        """Relink every stale latest record (only ``source_id``'s, when given) and store the run.

        A record is stale when it has no link row or its links were made against another
        mirror load. Without a loaded mirror nothing is linked (every record would read "not
        linked" for a reason that is ours, not the record's) and the stored run says so.
        Phases: ``candidates`` (finding the stale records and their candidates), ``scoring``
        (off the event loop) and ``writing``.
        """
        started, cpu_started = self._clock.now(), cpu_seconds()
        mirror = await latest_mirror_load(self._connectors, self._snapshots)
        if mirror is None:
            logger.warning("npi linking skipped trigger=%s: mirror never loaded", trigger)
            return await self._store(
                started, cpu_started, trigger, None, Counter(), {}, note=MIRROR_NOT_LOADED
            )
        phases: dict[str, timedelta] = {}
        with phase(phases, "candidates"):
            stale = await self._links.stale_records(
                source_id=source_id, mirror_snapshot_id=mirror.id
            )
        best: Counter[MatchLevel] = Counter()
        for number, start in enumerate(range(0, len(stale), self._batch_size), start=1):
            batch = stale[start : start + self._batch_size]
            with phase(phases, "candidates"):
                found = await self._links.candidates(batch)
            with phase(phases, "scoring"):
                links = await asyncio.to_thread(_score, found, mirror.id, self._clock.now())
            with phase(phases, "writing"):
                await self._links.replace_links(links)
            levels = _best_levels(links)
            best.update(levels.values())
            logger.info(
                "npi linking batch=%d records=%d links=%d linked=%d trigger=%s",
                number,
                len(levels),
                len(links),
                sum(1 for level in levels.values() if level is not MatchLevel.NONE),
                trigger,
            )
        return await self._store(started, cpu_started, trigger, mirror.id, best, phases)

    async def summary(self) -> NpiLinkSummary:
        """Where linking stands over the latest records, and the last run."""
        return await self._links.summary()

    async def _store(
        self,
        started: datetime,
        cpu_started: float,
        trigger: str,
        mirror_snapshot_id: UUID | None,
        best: Counter[MatchLevel],
        phases: dict[str, timedelta],
        *,
        note: str | None = None,
    ) -> NpiLinkRun:
        run = NpiLinkRun(
            id=uuid4(),
            started_at=started,
            finished_at=max(self._clock.now(), started),
            trigger=trigger,
            mirror_snapshot_id=mirror_snapshot_id,
            records=sum(best.values()),
            linked=sum(count for level, count in best.items() if level is not MatchLevel.NONE),
            by_level={level: best[level] for level in MatchLevel},
            phases=phases,
            cpu_time=timedelta(seconds=cpu_seconds() - cpu_started),
            note=note,
        )
        await self._links.add_run(run)
        logger.info(
            "npi linking done trigger=%s records=%d linked=%d seconds=%.1f",
            trigger,
            run.records,
            run.linked,
            run.duration.total_seconds(),
        )
        return run


def _score(
    found: Sequence[LinkCandidates], mirror_snapshot_id: UUID, now: datetime
) -> list[NpiLink]:
    """Every record's links, as the domain decides them; pure CPU, run in a worker thread."""
    return [
        NpiLink(
            id=uuid4(),
            record_id=item.record.id,
            npi=npi,
            match=match,
            candidates=item.found,
            mirror_snapshot_id=mirror_snapshot_id,
            linked_at=now,
        )
        for item in found
        for npi, match in link_record_to_npis(item.record, item.candidates, item.found)
    ]


def _best_levels(links: Sequence[NpiLink]) -> dict[UUID, MatchLevel]:
    """Each record's best level: its first link, since the domain orders them best first."""
    best: dict[UUID, MatchLevel] = {}
    for link in links:
        best.setdefault(link.record_id, link.level)
    return best
