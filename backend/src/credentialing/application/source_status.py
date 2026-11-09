"""Use case: the health of every registered source, so one place shows each link and its data.

For each bulk-list or dataset source it reports the latest fetch and the latest applied list,
how many records are current, how old the data is, when the next fetch is due, and why the
last fetch failed when it did; for a dataset source also what its last load did. The live NPI
registry, asked on demand and never polled, is reported apart: its requests over the last
:data:`ON_DEMAND_WINDOW` and the last one.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict

from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    Freshness,
    Outcome,
)
from credentialing.domain.models import DatasetLoad, SourceSnapshot
from credentialing.domain.policies import FreshnessPolicyTable, poll_anchor
from credentialing.domain.ports import (
    BulkListConnector,
    Clock,
    ConnectorDirectory,
    DatasetConnector,
    ExclusionRecordRepository,
    NpiRegistryRepository,
    SnapshotRepository,
)
from credentialing.domain.types import SourceId, StateCode


class SourceHealth(StrEnum):
    """How a source is doing, from the monitoring point of view."""

    OK = "ok"
    OVERDUE = "overdue"
    FAILING = "failing"
    NEVER_FETCHED = "never_fetched"


class SourceStatus(BaseModel):
    """One source's links, its latest fetches and the state of its data."""

    model_config = ConfigDict(frozen=True)

    source_id: SourceId
    display_name: str
    credential_type: CredentialType
    jurisdictions: tuple[StateCode, ...]
    access_policy: AccessPolicy
    change_signal: ChangeSignal
    poll_interval: timedelta
    download_url: str | None
    source_url: str
    latest: SourceSnapshot | None
    """The most recent fetch, whatever its outcome."""
    latest_completed: SourceSnapshot | None
    """The most recent ``COMPLETED`` fetch: the list checks rely on."""
    current_records: int
    """Current rows of a list; for a dataset source, the NPIs its mirror lists (the same for
    every source feeding that mirror)."""
    health: SourceHealth
    freshness: Freshness | None
    """Freshness of the applied list under the central policy; None when never fetched."""
    freshness_max_age: timedelta
    """The freshness window applied to this source, from the central policy table."""
    data_as_of: AwareDatetime | None
    """When the agency last updated the applied list, only as the source itself states it
    (its ``Last-Modified`` header or API metadata); None when it states none. Never the fetch
    time, which is ``latest_completed.fetched_at``."""
    next_due: AwareDatetime | None
    """When the next poll is due: the last completed fetch, or a later ``BLOCKED`` one, plus
    the poll interval; None while only failed or no fetches exist (due now)."""
    problem: str | None
    """The latest fetch's note when that fetch did not complete (also before any list loaded)."""
    last_load: DatasetLoad | None = None
    """A dataset source's most recent load (counts, phase timings, CPU time); None for a bulk
    list and before any file was loaded. Unchanged polls load nothing, so it is usually older
    than ``latest_completed``."""


ON_DEMAND_WINDOW: Final = timedelta(days=7)
"""How far back the on-demand usage line counts requests."""


class OnDemandUsage(BaseModel):
    """How an on-demand source (the live NPI registry) has been used: requests since
    ``since``, how many did not complete, and the latest request whatever its outcome."""

    model_config = ConfigDict(frozen=True)

    source_id: SourceId
    display_name: str
    since: AwareDatetime
    requests: int
    failed: int
    last: SourceSnapshot | None


class SourceStatusReport:
    """Builds a :class:`SourceStatus` for every registered bulk-list and dataset source.

    ``npi_registry`` is the mirror dataset sources load into; a report over dataset sources
    without it is a wiring error (``TypeError``).
    """

    def __init__(
        self,
        connectors: ConnectorDirectory,
        snapshots: SnapshotRepository,
        records: ExclusionRecordRepository,
        freshness: FreshnessPolicyTable,
        clock: Clock,
        *,
        npi_registry: NpiRegistryRepository | None = None,
    ) -> None:
        self._connectors = connectors
        self._snapshots = snapshots
        self._records = records
        self._freshness = freshness
        self._clock = clock
        self._npi_registry = npi_registry

    async def run(self) -> tuple[SourceStatus, ...]:
        """Return every source's status, ordered by display name.

        The mirror is counted once per report, whatever the number of dataset sources.
        """
        now = self._clock.now()
        connectors = self._connectors.polled_connectors()
        mirrored = await self._mirror_count(connectors)
        statuses = [await self._status(c, now, mirrored) for c in connectors]
        return tuple(sorted(statuses, key=lambda s: (s.display_name.casefold(), s.source_id)))

    async def npi_api_usage(self) -> OnDemandUsage | None:
        """The live NPI registry's requests over :data:`ON_DEMAND_WINDOW` and its latest one;
        None when no live registry connector is registered."""
        connectors = self._connectors.npi_registry_connectors()
        if not connectors:
            return None
        capabilities = connectors[0].capabilities
        since = self._clock.now() - ON_DEMAND_WINDOW
        requests, failed = await self._snapshots.count_since(capabilities.source_id, since)
        return OnDemandUsage(
            source_id=capabilities.source_id,
            display_name=capabilities.display_name,
            since=since,
            requests=requests,
            failed=failed,
            last=await self._snapshots.latest(capabilities.source_id),
        )

    async def _mirror_count(
        self, connectors: Sequence[BulkListConnector | DatasetConnector]
    ) -> int:
        if not any(isinstance(c, DatasetConnector) for c in connectors):
            return 0
        if self._npi_registry is None:
            raise TypeError("SourceStatusReport needs an npi_registry to report dataset sources")
        return await self._npi_registry.count()

    async def _status(
        self, connector: BulkListConnector | DatasetConnector, now: datetime, mirrored: int
    ) -> SourceStatus:
        capabilities = connector.capabilities
        source_id = capabilities.source_id
        latest = await self._snapshots.latest(source_id)
        completed = await self._snapshots.latest_completed(source_id)
        dataset = isinstance(connector, DatasetConnector)
        loaded = await self._snapshots.latest_loaded(source_id) if dataset else None
        policy = self._freshness.policy_for(capabilities.credential_type, source_id)
        failed = latest is not None and latest.outcome is not Outcome.COMPLETED
        anchor = poll_anchor(latest, completed)
        next_due = anchor.fetched_at + capabilities.poll_interval if anchor else None

        if completed is None:
            health = SourceHealth.NEVER_FETCHED
        elif failed:
            health = SourceHealth.FAILING
        elif next_due is not None and now > next_due:
            health = SourceHealth.OVERDUE
        else:
            health = SourceHealth.OK

        return SourceStatus(
            source_id=source_id,
            display_name=capabilities.display_name,
            credential_type=capabilities.credential_type,
            jurisdictions=tuple(sorted(capabilities.jurisdictions)),
            access_policy=capabilities.access_policy,
            change_signal=capabilities.change_signal,
            poll_interval=capabilities.poll_interval,
            download_url=capabilities.download_url,
            source_url=capabilities.source_url,
            latest=latest,
            latest_completed=completed,
            current_records=(mirrored if dataset else await self._records.count_current(source_id)),
            health=health,
            freshness=(
                policy.evaluate(completed.fetched_at, now, capabilities.method)
                if completed
                else None
            ),
            freshness_max_age=policy.max_age,
            data_as_of=completed.last_modified if completed else None,
            next_due=next_due,
            problem=latest.note if failed and latest else None,
            last_load=loaded.load if loaded else None,
        )
