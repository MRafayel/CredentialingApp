"""In-memory implementations of the domain ports, for fast tests of use cases and the API."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from credentialing.connectors.framework.http import FetchOutcome
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    FetchResult,
    Provider,
    SnapshotDiff,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.names import normalize_name
from credentialing.domain.policies import candidate_keys
from credentialing.domain.types import Npi, SourceId


class FixedClock:
    """A clock that stays where a test puts it."""

    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def capabilities(
    source_id: str = "fake_federal",
    jurisdictions: frozenset[str] = frozenset({"US"}),
    *,
    display_name: str = "Fake Federal List",
) -> ConnectorCapabilities:
    """Made-up capabilities of an exclusion list."""
    return ConnectorCapabilities(
        source_id=source_id,
        credential_type=CredentialType.EXCLUSION,
        jurisdictions=jurisdictions,
        access_policy=AccessPolicy.HTTP,
        change_signal=ChangeSignal.CONTENT_HASH,
        method=VerificationMethod.BULK_DATASET,
        poll_interval=timedelta(days=7),
        source_url=f"https://example.test/{source_id}",
        display_name=display_name,
        download_url=f"https://example.test/{source_id}/list.csv",
    )


class ScriptedConnector:
    """A bulk-list connector that returns prepared fetch results in order."""

    def __init__(self, capabilities: ConnectorCapabilities, *results: FetchResult) -> None:
        self._capabilities = capabilities
        self._results = list(results)
        self.previous_seen: list[SourceSnapshot | None] = []

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return self._capabilities

    def then(self, result: FetchResult) -> None:
        self._results.append(result)

    async def fetch(self, previous: SourceSnapshot | None) -> FetchResult:
        self.previous_seen.append(previous)
        return self._results.pop(0)


class ScriptedFetcher:
    """A :class:`~credentialing.connectors.framework.http.Fetcher` returning one outcome.

    Without an outcome it is for tests that only call ``parse``, and fails if fetched.
    """

    def __init__(self, outcome: FetchOutcome | None = None) -> None:
        self.outcome = outcome

    async def fetch(
        self, url: str, *, previous: SourceSnapshot | None, change_signal: ChangeSignal
    ) -> FetchOutcome:
        assert self.outcome is not None, "this fetcher has no scripted outcome"
        return self.outcome


class InMemorySnapshots:
    def __init__(self) -> None:
        self.stored: list[SourceSnapshot] = []

    async def add(self, snapshot: SourceSnapshot) -> None:
        self.stored.append(snapshot)

    async def latest(self, source_id: SourceId) -> SourceSnapshot | None:
        own = [s for s in self.stored if s.source_id == source_id]
        return max(own, key=lambda s: s.fetched_at) if own else None

    async def latest_completed(self, source_id: SourceId) -> SourceSnapshot | None:
        own = [
            s for s in self.stored if s.source_id == source_id and s.outcome is Outcome.COMPLETED
        ]
        return max(own, key=lambda s: s.fetched_at) if own else None


class InMemoryRecords:
    """Current rows only, keyed by identity; enough to exercise the use cases."""

    def __init__(self) -> None:
        self.current: dict[tuple[str, str], ExclusionRecord] = {}
        self.all: dict[UUID, ExclusionRecord] = {}

    async def replace_current(
        self,
        source_id: SourceId,
        snapshot_id: UUID,
        records: Sequence[ExclusionRecord],
        now: datetime,
    ) -> SnapshotDiff:
        incoming = {record.record_hash: record for record in reversed(records)}
        mine = {h: r for (s, h), r in self.current.items() if s == source_id}
        added = [r for h, r in incoming.items() if h not in mine]
        changed = [
            r for h, r in incoming.items() if h in mine and mine[h].content_hash != r.content_hash
        ]
        removed = mine.keys() - incoming.keys()
        for hash_ in removed:
            del self.current[(source_id, hash_)]
        for record in added + changed:
            self.current[(source_id, record.record_hash)] = record
            self.all[record.id] = record
        return SnapshotDiff(
            added=len(added),
            changed=len(changed),
            removed=len(removed),
            unchanged=len(incoming) - len(added) - len(changed),
        )

    async def find_candidates(
        self, npi: Npi | None, last_name: str | None, organization_name: str | None
    ) -> Sequence[ExclusionRecord]:
        keys = {normalize_name(n) for n in (last_name, organization_name) if n} - {""}
        return [
            record
            for record in self.current.values()
            if (npi is not None and record.npi == npi) or candidate_keys(record) & keys
        ]

    async def get_many(self, ids: Sequence[UUID]) -> Sequence[ExclusionRecord]:
        return [self.all[i] for i in ids if i in self.all]

    async def count_current(self, source_id: SourceId) -> int:
        return sum(1 for (source, _) in self.current if source == source_id)


class InMemoryProviders:
    def __init__(self, *providers: Provider) -> None:
        self.stored = {provider.id: provider for provider in providers}

    async def add(self, provider: Provider) -> None:
        self.stored[provider.id] = provider

    async def get(self, organization_id: UUID, provider_id: UUID) -> Provider | None:
        provider = self.stored.get(provider_id)
        return provider if provider and provider.organization_id == organization_id else None

    async def find_by_npi(self, organization_id: UUID, npi: Npi) -> Provider | None:
        return next(
            (
                p
                for p in self.stored.values()
                if p.organization_id == organization_id and p.npi == npi
            ),
            None,
        )


class InMemoryVerifications:
    def __init__(self) -> None:
        self.stored: list[VerificationResult] = []

    async def add(self, result: VerificationResult) -> None:
        self.stored.append(result)

    async def latest(
        self,
        organization_id: UUID,
        provider_id: UUID,
        credential_type: CredentialType,
        source_id: SourceId,
    ) -> VerificationResult | None:
        history = [
            r
            for r in await self.history(organization_id, provider_id, credential_type)
            if r.source_id == source_id
        ]
        return history[0] if history else None

    async def history(
        self, organization_id: UUID, provider_id: UUID, credential_type: CredentialType
    ) -> Sequence[VerificationResult]:
        return [
            r
            for r in await self.history_all(organization_id, provider_id)
            if r.credential_type is credential_type
        ]

    async def history_all(
        self, organization_id: UUID, provider_id: UUID
    ) -> Sequence[VerificationResult]:
        own = [
            r
            for r in self.stored
            if r.organization_id == organization_id and r.provider_id == provider_id
        ]
        return sorted(own, key=lambda r: r.created_at, reverse=True)
