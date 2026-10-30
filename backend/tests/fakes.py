"""In-memory implementations of the domain ports, for fast tests of use cases and the API."""

import re
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
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.policies import (
    candidate_keys,
    license_key,
    pair_rewritten_rows,
    party_key,
)
from credentialing.domain.types import Npi, SourceId, StateCode

_WORDS = re.compile(r"[^\W_]+")


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
    """A :class:`~credentialing.connectors.framework.http.Fetcher` returning prepared outcomes.

    The outcomes are returned in order and the last one answers every further request (a
    landing page or API answer first, then the file). Without an outcome it is for tests that
    only call ``parse``, and fails if fetched. ``requests`` records each URL and change signal.
    """

    def __init__(self, *outcomes: FetchOutcome) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[tuple[str, ChangeSignal]] = []

    async def fetch(
        self,
        url: str,
        *,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
    ) -> FetchOutcome:
        assert self.outcomes, "this fetcher has no scripted outcome"
        self.requests.append((url, change_signal))
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]


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
    """Current and latest rows keyed by identity; enough to exercise the use cases.

    ``latest`` holds the newest version of each identity, current or removed from the list
    (``valid_to`` set), as the database's ``latest`` flag does; reads go through it.
    """

    def __init__(self) -> None:
        self.current: dict[tuple[str, str], ExclusionRecord] = {}
        self.latest: dict[tuple[str, str], ExclusionRecord] = {}
        self.all: dict[UUID, ExclusionRecord] = {}

    async def replace_current(
        self,
        source_id: SourceId,
        snapshot_id: UUID,
        records: Sequence[ExclusionRecord],
        now: datetime,
        *,
        source_changed: bool,
    ) -> SnapshotDiff:
        incoming = {record.record_hash: record for record in reversed(records)}
        mine = {h: r for (s, h), r in self.current.items() if s == source_id}
        new = [r for h, r in incoming.items() if h not in mine]
        readded = [r for r in new if (source_id, r.record_hash) in self.latest]
        fresh = [r for r in new if (source_id, r.record_hash) not in self.latest]
        changed = [
            r for h, r in incoming.items() if h in mine and mine[h].content_hash != r.content_hash
        ]
        vanished = mine.keys() - incoming.keys()
        pairs = (
            pair_rewritten_rows(
                {h: (mine[h].npi, mine[h].effective_date) for h in vanished},
                {r.record_hash: (r.npi, r.effective_date) for r in fresh},
            )
            if source_changed
            else {}
        )
        added = [r for r in fresh if r.record_hash not in pairs]
        rewritten = [
            r.model_copy(update={"rewrites": mine[pairs[r.record_hash]].id})
            for r in fresh
            if r.record_hash in pairs
        ]
        removed = vanished - set(pairs.values())
        for hash_ in vanished | {r.record_hash for r in changed}:
            closed = self.current.pop((source_id, hash_)).model_copy(update={"valid_to": now})
            self.all[closed.id] = closed
            if hash_ not in vanished or (source_changed and hash_ in removed):
                self.latest[(source_id, hash_)] = closed
            else:
                del self.latest[(source_id, hash_)]
        for record in added + rewritten + readded + changed:
            self.current[(source_id, record.record_hash)] = record
            self.latest[(source_id, record.record_hash)] = record
            self.all[record.id] = record
        return SnapshotDiff(
            added=len(added),
            changed=len(changed),
            removed=len(removed) if source_changed else 0,
            unchanged=len(incoming) - len(new) - len(changed),
            readded=len(readded),
            reparsed=0 if source_changed else len(removed),
            rewritten=len(rewritten),
        )

    async def find_candidates(
        self,
        npi: Npi | None,
        last_name: str | None,
        organization_name: str | None,
        *,
        license_numbers: Sequence[str] = (),
    ) -> Sequence[ExclusionRecord]:
        keys = {normalize_name(last_name)} if last_name else set()
        if organization_name:
            keys.add(normalize_organization_name(organization_name))
        keys.discard("")
        licenses = {license_key(number) for number in license_numbers} - {""}
        return [
            record
            for record in self.latest.values()
            if (npi is not None and npi in (record.npi, *record.related_npis))
            or candidate_keys(record) & keys
            or (record.license is not None and license_key(record.license.number) in licenses)
        ]

    async def search(
        self,
        query: str,
        *,
        limit: int,
        source_ids: Sequence[SourceId] = (),
        state: StateCode | None = None,
    ) -> Sequence[ExclusionRecord]:
        """Every word of the query starts a word of the record's names or identifiers.

        With ``state``, only records from ``source_ids`` or with a licence or address there.
        """
        term = query.strip()
        tokens = _WORDS.findall(term.casefold())
        if not tokens:
            return []

        def words(record: ExclusionRecord) -> list[str]:
            name = record.person_name
            texts = [
                name.last if name else None,
                name.first if name else None,
                name.middle if name else None,
                record.organization_name,
                *record.aliases,
                *record.related_parties,
                record.npi,
                *record.related_npis,
                record.state_provider_id,
                record.license.number if record.license else None,
            ]
            return _WORDS.findall(" ".join(t for t in texts if t).casefold())

        def in_state(record: ExclusionRecord) -> bool:
            return (
                state is None
                or record.source_id in source_ids
                or (record.license is not None and record.license.state == state)
                or (record.address is not None and record.address.state == state)
            )

        def hit(record: ExclusionRecord) -> bool:
            own = words(record)
            matched = record.npi == term or all(any(w.startswith(t) for w in own) for t in tokens)
            return matched and in_state(record)

        found = sorted(
            (r for r in self.latest.values() if hit(r)),
            key=lambda r: (
                r.npi != term,
                r.organization_name or (r.person_name.last if r.person_name else ""),
                str(r.id),
            ),
        )
        return found[: min(max(limit, 1), 50)]

    async def get(self, record_id: UUID) -> ExclusionRecord | None:
        return self.all.get(record_id)

    async def get_many(self, ids: Sequence[UUID]) -> Sequence[ExclusionRecord]:
        return [self.all[i] for i in ids if i in self.all]

    async def versions(self, source_id: SourceId, record_hash: str) -> Sequence[ExclusionRecord]:
        own = [
            r for r in self.all.values() if (r.source_id, r.record_hash) == (source_id, record_hash)
        ]
        return sorted(own, key=lambda r: (r.valid_from, str(r.id)))

    async def rewritten_by(self, record_id: UUID) -> ExclusionRecord | None:
        return next((r for r in self.all.values() if r.rewrites == record_id), None)

    async def same_name(self, record: ExclusionRecord, *, limit: int) -> Sequence[ExclusionRecord]:
        key = party_key(record)
        first = record.person_name.first.casefold() if record.person_name else None
        found = [
            r
            for r in self.latest.values()
            if key is not None and r.id != record.id and key in candidate_keys(r)
        ]

        def rank(r: ExclusionRecord) -> tuple[bool, str, str]:
            same_first = r.person_name is not None and r.person_name.first.casefold() == first
            return (not same_first, r.source_id, str(r.id))

        return sorted(found, key=rank)[: max(limit, 0)]

    async def same_license(
        self, record: ExclusionRecord, *, limit: int
    ) -> Sequence[ExclusionRecord]:
        own = record.license
        found = [
            r
            for r in self.latest.values()
            if own is not None
            and r.id != record.id
            and r.license is not None
            and r.license.state == own.state
            and license_key(r.license.number) == license_key(own.number)
        ]
        return sorted(found, key=lambda r: (r.source_id, str(r.id)))[: max(limit, 0)]

    async def linked_by_npi(
        self, npis: Sequence[Npi], *, exclude_id: UUID
    ) -> Sequence[ExclusionRecord]:
        wanted = set(npis)
        found = [
            r
            for r in self.latest.values()
            if r.id != exclude_id and wanted & {*(r.related_npis), *([r.npi] if r.npi else [])}
        ]
        return sorted(found, key=lambda r: (r.source_id, str(r.id)))

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

    async def list(
        self, organization_id: UUID, *, query: str | None, limit: int, offset: int
    ) -> tuple[Sequence[Provider], int]:
        term = query.strip().casefold() if query else ""

        def matches(provider: Provider) -> bool:
            name = provider.person_name
            texts = (
                name.last if name else None,
                name.first if name else None,
                provider.organization_name,
            )
            return not term or provider.npi == term or any(term in t.casefold() for t in texts if t)

        own = sorted(
            (
                p
                for p in self.stored.values()
                if p.organization_id == organization_id and matches(p)
            ),
            key=lambda p: (p.created_at, p.id),
            reverse=True,
        )
        return own[offset : offset + limit], len(own)


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

    async def latest_per_source(
        self, organization_id: UUID, provider_id: UUID, credential_type: CredentialType
    ) -> Sequence[VerificationResult]:
        newest: dict[str, VerificationResult] = {}
        for result in await self.history(organization_id, provider_id, credential_type):
            newest.setdefault(result.source_id, result)
        return [newest[source_id] for source_id in sorted(newest)]

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
