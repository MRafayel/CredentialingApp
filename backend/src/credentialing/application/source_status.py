"""Use case: the health of every registered source, so one place shows each link and its data.

For each bulk-list source it reports the latest fetch and the latest applied list, how many
records are current, how old the data is, when the next fetch is due, and why the last fetch
failed when it did.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict

from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    Freshness,
    Outcome,
)
from credentialing.domain.models import SourceSnapshot
from credentialing.domain.policies import FreshnessPolicyTable
from credentialing.domain.ports import (
    BulkListConnector,
    Clock,
    ConnectorDirectory,
    ExclusionRecordRepository,
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
    health: SourceHealth
    freshness: Freshness | None
    """Freshness of the applied list under the central policy; None when never fetched."""
    freshness_max_age: timedelta
    """The freshness window applied to this source, from the central policy table."""
    data_as_of: AwareDatetime | None
    """The source's own last-modified time, or when the applied list was last fetched."""
    next_due: AwareDatetime | None
    """When the next poll is due: the last completed fetch plus the poll interval."""
    problem: str | None
    """The latest fetch's note when that fetch did not complete (also before any list loaded)."""


class SourceStatusReport:
    """Builds a :class:`SourceStatus` for every registered bulk-list source."""

    def __init__(
        self,
        connectors: ConnectorDirectory,
        snapshots: SnapshotRepository,
        records: ExclusionRecordRepository,
        freshness: FreshnessPolicyTable,
        clock: Clock,
    ) -> None:
        self._connectors = connectors
        self._snapshots = snapshots
        self._records = records
        self._freshness = freshness
        self._clock = clock

    async def run(self) -> tuple[SourceStatus, ...]:
        """Return every source's status, ordered by display name."""
        now = self._clock.now()
        statuses = [await self._status(c, now) for c in self._connectors.bulk_connectors()]
        return tuple(sorted(statuses, key=lambda s: (s.display_name.casefold(), s.source_id)))

    async def _status(self, connector: BulkListConnector, now: datetime) -> SourceStatus:
        capabilities = connector.capabilities
        source_id = capabilities.source_id
        latest = await self._snapshots.latest(source_id)
        completed = await self._snapshots.latest_completed(source_id)
        policy = self._freshness.policy_for(capabilities.credential_type, source_id)
        failed = latest is not None and latest.outcome is not Outcome.COMPLETED
        next_due = completed.fetched_at + capabilities.poll_interval if completed else None

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
            current_records=await self._records.count_current(source_id),
            health=health,
            freshness=(
                policy.evaluate(completed.fetched_at, now, capabilities.method)
                if completed
                else None
            ),
            freshness_max_age=policy.max_age,
            data_as_of=(completed.last_modified or completed.fetched_at) if completed else None,
            next_due=next_due,
            problem=latest.note if failed and latest else None,
        )
