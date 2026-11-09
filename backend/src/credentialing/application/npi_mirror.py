"""Writing live NPI registry answers into the local mirror: newer wins, nothing is lost.

An answer is stored when the mirror has no row for its NPI or an older one
(:func:`~credentialing.domain.policies.records_to_store`, which also carries the mirror's
deactivation history forward), through the same loader as the files (an ``INCREMENTAL`` load
under the answer's snapshot), and what the load did is stored on that snapshot. A row written
from the API has an ``api:`` line hash, so the next file load rewrites it: one ``changed`` per
such NPI, which restores any field the API does not give. Nothing is ever written for "no
result": the API cannot tell a deactivated NPI from one never issued.
"""

from collections.abc import Iterator, Sequence
from collections.abc import Set as AbstractSet
from datetime import timedelta
from uuid import UUID

from credentialing.application.measure import cpu_seconds
from credentialing.domain.enums import DatasetLoadMode
from credentialing.domain.models import DatasetLoad, NpiRecord
from credentialing.domain.policies import carry_forward, records_to_store
from credentialing.domain.ports import Clock, NpiRegistryRepository, SnapshotRepository
from credentialing.domain.types import Npi


class _RecordsDataset:
    """A :class:`~credentialing.domain.ports.Dataset` over records already in memory."""

    def __init__(self, records: Sequence[NpiRecord]) -> None:
        self._records = records

    def scan(self) -> Iterator[tuple[Npi, str]]:
        for record in self._records:
            yield record.npi, record.line_hash

    def records(self, npis: AbstractSet[Npi] | None) -> Iterator[NpiRecord]:
        return (r for r in self._records if npis is None or r.npi in npis)

    def close(self) -> None:
        """Nothing to release."""


class MirrorWriter:
    """Stores live answers into the mirror and records each load on its answer's snapshot."""

    def __init__(
        self, registry: NpiRegistryRepository, snapshots: SnapshotRepository, clock: Clock
    ) -> None:
        self._registry = registry
        self._snapshots = snapshots
        self._clock = clock

    async def store(
        self, answers: Sequence[NpiRecord], *, snapshot_id: UUID, request_time: timedelta
    ) -> tuple[tuple[NpiRecord, ...], DatasetLoad | None]:
        """Store what is newer than the mirror, in one load under ``snapshot_id`` (which must
        already be stored), and return the answers as the mirror now sees them (deactivation
        history carried forward) with the load, or None when nothing was newer.

        The load's phases start with ``request`` (``request_time``, the API call), its CPU
        time covers the comparison and the load.
        """
        cpu_started = cpu_seconds()
        mirrored = {r.npi: r for r in await self._registry.get_many([a.npi for a in answers])}
        seen = tuple(carry_forward(answer, mirrored.get(answer.npi)) for answer in answers)
        to_store = records_to_store(answers, mirrored)
        if not to_store:
            return seen, None
        load = await self._registry.load(
            _RecordsDataset(to_store),
            mode=DatasetLoadMode.INCREMENTAL,
            snapshot_id=snapshot_id,
            now=self._clock.now(),
        )
        load = load.model_copy(
            update={
                "phases": {"request": request_time, **load.phases},
                "cpu_time": timedelta(seconds=cpu_seconds() - cpu_started),
            }
        )
        await self._snapshots.set_load(snapshot_id, load)
        return seen, load
