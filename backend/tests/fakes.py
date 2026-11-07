"""In-memory implementations of the domain ports, for fast tests of use cases and the API."""

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import UUID, uuid4

from credentialing.connectors.framework.http import (
    FetchedFile,
    FetchFailure,
    FetchOutcome,
    FileOutcome,
    NotModified,
    Response,
    Session,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    DatasetLoadMode,
    EntityKind,
    MatchLevel,
    NpiStatus,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.errors import DatasetShrankError
from credentialing.domain.models import (
    ConnectorCapabilities,
    DatasetLoad,
    ExclusionRecord,
    FetchResult,
    NpiLink,
    NpiLinkRun,
    NpiLinkSummary,
    NpiRecord,
    Provider,
    SnapshotDiff,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.policies import (
    CANDIDATE_CAP,
    candidate_keys,
    level_rank,
    license_key,
    pair_rewritten_rows,
    party_key,
)
from credentialing.domain.ports import Dataset, DatasetFetch, LinkCandidates
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


SESSION_OPENED_AT = datetime(2026, 1, 15, 12, 0, 30, tzinfo=UTC)
"""When every scripted session opens."""


@dataclass(frozen=True, slots=True)
class SessionRequest:
    """One request a scripted session received."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str] = field(default_factory=dict)
    json: object | None = None


class ScriptedSession:
    """A :class:`~credentialing.connectors.framework.http.Session` answering from a script."""

    def __init__(self, answers: list[Response | FetchFailure], log: list[SessionRequest]) -> None:
        self._answers = answers
        self._log = log
        self.closed = False

    @property
    def opened_at(self) -> datetime:
        return SESSION_OPENED_AT

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        json: object | None = None,
    ) -> Response | FetchFailure:
        assert self._answers, f"no scripted answer for {method} {url}"
        self._log.append(SessionRequest(method, url, dict(headers or {}), dict(params or {}), json))
        return self._answers.pop(0)


FILE_FETCHED_AT = datetime(2026, 1, 15, 12, 1, tzinfo=UTC)
"""When every scripted file download happens."""

FILE_LAST_MODIFIED = datetime(2026, 1, 14, 3, 0, tzinfo=UTC)
"""The ``Last-Modified`` of every scripted file."""


@dataclass(frozen=True, slots=True)
class FileRequest:
    """One file download a scripted fetcher received."""

    url: str
    change_signal: ChangeSignal
    destination: Path
    crawl_delay: timedelta


class BrowserOptions(NamedTuple):
    """The browser options one :meth:`ScriptedFetcher.fetch` call received."""

    profile: str | None
    webdriver_disabled: bool
    challenge_timeout: timedelta | None


class ScriptedFetcher:
    """A :class:`~credentialing.connectors.framework.http.SessionFetcher` returning prepared
    outcomes.

    The outcomes are returned in order and the last one answers every further request (a
    landing page or API answer first, then the file). Without an outcome it is for tests that
    only call ``parse``, and fails if fetched. ``requests`` records each URL and change signal,
    ``entry_urls`` each request's warm-up page (``None`` when there was none),
    ``browser_options`` each request's browser options (profile, marker, challenge timeout).

    ``session_answers`` answer the requests of the sessions it opens, in order and each once;
    ``session_requests`` records them, ``sessions`` the sessions opened.

    ``files`` answer :meth:`download` in order, the last one every further download: bytes
    are written to the destination and returned as a :class:`FetchedFile`; ``downloads``
    records each request.
    """

    def __init__(
        self,
        *outcomes: FetchOutcome,
        session_answers: Sequence[Response | FetchFailure] = (),
        files: Sequence[bytes | NotModified | FetchFailure] = (),
    ) -> None:
        self.outcomes = list(outcomes)
        self.files = list(files)
        self.downloads: list[FileRequest] = []
        self.requests: list[tuple[str, ChangeSignal]] = []
        self.entry_urls: list[str | None] = []
        self.browser_options: list[BrowserOptions] = []
        self.session_answers = list(session_answers)
        self.session_requests: list[SessionRequest] = []
        self.sessions: list[ScriptedSession] = []
        self.crawl_delays: list[timedelta] = []

    @asynccontextmanager
    async def session(self, *, crawl_delay: timedelta) -> AsyncIterator[Session]:
        self.crawl_delays.append(crawl_delay)
        session = ScriptedSession(self.session_answers, self.session_requests)
        self.sessions.append(session)
        try:
            yield session
        finally:
            session.closed = True

    async def fetch(
        self,
        url: str,
        *,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
        entry_url: str | None = None,
        profile: str | None = None,
        webdriver_disabled: bool = False,
        challenge_timeout: timedelta | None = None,
    ) -> FetchOutcome:
        assert self.outcomes, "this fetcher has no scripted outcome"
        self.requests.append((url, change_signal))
        self.entry_urls.append(entry_url)
        self.browser_options.append(BrowserOptions(profile, webdriver_disabled, challenge_timeout))
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]

    async def download(
        self,
        url: str,
        *,
        destination: Path,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
    ) -> FileOutcome:
        assert self.files, "this fetcher has no scripted file"
        self.downloads.append(FileRequest(url, change_signal, destination, crawl_delay))
        answer = self.files.pop(0) if len(self.files) > 1 else self.files[0]
        if not isinstance(answer, bytes):
            return answer
        await asyncio.to_thread(_write, destination, answer)
        return FetchedFile(
            path=destination,
            content_hash=hashlib.sha256(answer).hexdigest(),
            etag='"file-v1"',
            last_modified=FILE_LAST_MODIFIED,
            content_type="application/zip",
            final_url=url,
            byte_size=len(answer),
            fetched_at=FILE_FETCHED_AT,
            status=200,
        )


def _write(destination: Path, body: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)


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

    async def latest_loaded(self, source_id: SourceId) -> SourceSnapshot | None:
        own = [s for s in self.stored if s.source_id == source_id and s.load is not None]
        return max(own, key=lambda s: s.fetched_at) if own else None

    async def set_load(self, snapshot_id: UUID, load: DatasetLoad) -> None:
        self.stored = [
            s.model_copy(update={"load": load, "row_count": load.scanned})
            if s.id == snapshot_id
            else s
            for s in self.stored
        ]


class ListDataset:
    """A :class:`~credentialing.domain.ports.Dataset` over records in memory.

    ``fail_reading`` raises in the second pass, as a file that breaks mid-way would;
    ``closed`` says whether the loader released it; ``asked`` records each second pass's
    NPI set.
    """

    def __init__(self, *records: NpiRecord, fail_reading: Exception | None = None) -> None:
        self._records = records
        self._fail_reading = fail_reading
        self.closed = False
        self.asked: list[AbstractSet[str] | None] = []

    def scan(self) -> Iterator[tuple[str, str]]:
        for record in self._records:
            yield record.npi, record.line_hash

    def records(self, npis: AbstractSet[str] | None) -> Iterator[NpiRecord]:
        self.asked.append(npis)
        for record in self._records:
            if self._fail_reading is not None:
                raise self._fail_reading
            if npis is None or record.npi in npis:
                yield record

    def close(self) -> None:
        self.closed = True


class ScriptedDatasetConnector:
    """A dataset connector that returns prepared fetches in order."""

    def __init__(self, capabilities: ConnectorCapabilities, *fetches: DatasetFetch) -> None:
        self._capabilities = capabilities
        self._fetches = list(fetches)
        self.previous_seen: list[SourceSnapshot | None] = []

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return self._capabilities

    async def fetch_dataset(self, previous: SourceSnapshot | None) -> DatasetFetch:
        self.previous_seen.append(previous)
        return self._fetches.pop(0)


def dataset_capabilities(source_id: str = "fake_dataset") -> ConnectorCapabilities:
    """Made-up capabilities of a dataset source."""
    return ConnectorCapabilities(
        source_id=source_id,
        credential_type=CredentialType.NPI,
        jurisdictions=frozenset({"US"}),
        access_policy=AccessPolicy.HTTP,
        change_signal=ChangeSignal.LINK_DISCOVERY,
        method=VerificationMethod.BULK_DATASET,
        poll_interval=timedelta(days=1),
        source_url=f"https://example.test/{source_id}",
        display_name=f"Fake {source_id} file",
    )


class InMemoryNpiRegistry:
    """The NPI mirror in a dict: enough to exercise the use cases.

    Rows are compared by line hash as the real loader does; ``fail`` raises from ``load``.
    ``loads`` records each load's mode, snapshot and ``min_rows``.
    """

    def __init__(self, *records: NpiRecord, fail: Exception | None = None) -> None:
        self.rows = {record.npi: record for record in records}
        self.fail = fail
        self.loads: list[tuple[DatasetLoadMode, UUID, int | None]] = []

    async def load(
        self,
        dataset: Dataset,
        *,
        mode: DatasetLoadMode,
        snapshot_id: UUID,
        now: datetime,
        min_rows: int | None = None,
    ) -> DatasetLoad:
        self.loads.append((mode, snapshot_id, min_rows))
        if self.fail is not None:
            raise self.fail
        hashes = dict(dataset.scan())
        if min_rows is not None and len(hashes) < min_rows:
            raise DatasetShrankError(len(hashes), min_rows)
        needed = {
            npi
            for npi, line_hash in hashes.items()
            if npi not in self.rows or self.rows[npi].line_hash != line_hash
        }
        added = len(needed - self.rows.keys())
        for record in dataset.records(needed):
            self.rows[record.npi] = record
        return DatasetLoad(
            mode=mode,
            scanned=len(hashes),
            added=added,
            changed=len(needed) - added,
            unchanged=len(hashes) - len(needed),
            phases={"scan": timedelta(seconds=2), "apply": timedelta(seconds=1)},
        )

    async def get(self, npi: Npi) -> NpiRecord | None:
        return self.rows.get(npi)

    async def count(self, *, status: NpiStatus | None = None) -> int:
        return sum(
            1
            for record in self.rows.values()
            if record.in_file and (status is None or record.status is status)
        )

    async def get_many(self, npis: Sequence[Npi]) -> Sequence[NpiRecord]:
        return [self.rows[npi] for npi in sorted(set(npis)) if npi in self.rows]

    async def search(self, query: str, *, limit: int) -> Sequence[NpiRecord]:
        """Exact NPI, or every word of the query in the row's names (enough for use cases)."""
        words = set(normalize_name(query).split())
        found = [
            record
            for record in self.rows.values()
            if record.in_file
            and (
                record.npi == query.strip()
                or (words and words <= set(normalize_name(_npi_names(record)).split()))
            )
        ]
        found.sort(key=lambda r: (r.status is not NpiStatus.ACTIVE, r.npi))
        return found[: min(limit, 50)]


def _npi_names(record: NpiRecord) -> str:
    name = record.person_name
    return " ".join(
        part
        for part in (
            name.last if name else None,
            name.first if name else None,
            record.organization_name,
        )
        if part
    )


class InMemoryNpiLinks:
    """Links in a dict over the in-memory records and mirror; enough for the use cases.

    Discovery is a plain stand-in for the SQL one: a record's published NPI, else mirror rows
    with the same normalized last and first name (or organization name). ``fail`` raises from
    ``replace_links``; ``asked`` records each ``candidates`` batch.
    """

    def __init__(self, records: InMemoryRecords, registry: InMemoryNpiRegistry) -> None:
        self._records = records
        self._registry = registry
        self.links: dict[UUID, list[NpiLink]] = {}
        self.runs: list[NpiLinkRun] = []
        self.fail: Exception | None = None
        self.asked: list[list[UUID]] = []

    async def stale_records(
        self,
        *,
        source_id: SourceId | None,
        mirror_snapshot_id: UUID | None,
        limit: int | None = None,
    ) -> Sequence[UUID]:
        stale = sorted(
            record.id
            for record in self._records.latest.values()
            if (source_id is None or record.source_id == source_id)
            and not any(
                link.mirror_snapshot_id == mirror_snapshot_id
                for link in self.links.get(record.id, [])
            )
        )
        return stale[:limit] if limit is not None else stale

    async def candidates(self, record_ids: Sequence[UUID]) -> Sequence[LinkCandidates]:
        self.asked.append(list(record_ids))
        found: list[LinkCandidates] = []
        for record_id in sorted(set(record_ids)):
            record = self._records.all.get(record_id)
            if record is None:
                continue
            if record.npi:
                own = self._registry.rows.get(record.npi)
                rows = [own] if own else []
            else:
                rows = [row for row in self._registry.rows.values() if _same_name(record, row)]
            rows.sort(key=lambda row: row.npi)
            found.append(
                LinkCandidates(record, rows[:CANDIDATE_CAP], min(len(rows), CANDIDATE_CAP + 1))
            )
        return found

    async def replace_links(self, links: Sequence[NpiLink]) -> None:
        if self.fail is not None:
            raise self.fail
        grouped: dict[UUID, list[NpiLink]] = {}
        for link in links:
            grouped.setdefault(link.record_id, []).append(link)
        self.links.update(grouped)

    async def links_for(self, record_ids: Sequence[UUID]) -> Mapping[UUID, Sequence[NpiLink]]:
        return {
            record_id: sorted(
                self.links[record_id], key=lambda link: (level_rank(link.level), link.npi or "")
            )
            for record_id in record_ids
            if record_id in self.links
        }

    async def summary(self) -> NpiLinkSummary:
        latest = list(self._records.latest.values())
        best: dict[MatchLevel, int] = {}
        never = 0
        for record in latest:
            links = self.links.get(record.id)
            if not links:
                never += 1
                continue
            level = min((link.level for link in links), key=level_rank)
            best[level] = best.get(level, 0) + 1
        return NpiLinkSummary(
            records=len(latest),
            by_best_level=best,
            never_checked=never,
            last_run=await self.last_run(),
        )

    async def add_run(self, run: NpiLinkRun) -> None:
        self.runs.append(run)

    async def last_run(self) -> NpiLinkRun | None:
        return max(self.runs, key=lambda run: run.finished_at) if self.runs else None


def _same_name(record: ExclusionRecord, row: NpiRecord) -> bool:
    if record.person_name and row.person_name and row.entity_kind is EntityKind.INDIVIDUAL:
        return normalize_name(record.person_name.last) == normalize_name(
            row.person_name.last
        ) and normalize_name(record.person_name.first) == normalize_name(row.person_name.first)
    if record.organization_name and row.organization_name:
        return normalize_organization_name(record.organization_name) == (
            normalize_organization_name(row.organization_name)
        )
    return False


class FakeLinker:
    """Stands in for :class:`~credentialing.application.link_npis.LinkNpis` in a sync: records
    each call and returns a fixed run, or raises ``fail``."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[SourceId | None, str]] = []
        self.fail = fail

    async def run(self, *, source_id: SourceId | None, trigger: str) -> NpiLinkRun:
        self.calls.append((source_id, trigger))
        if self.fail is not None:
            raise self.fail
        return NpiLinkRun(
            id=uuid4(),
            started_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 1, 15, 12, 1, tzinfo=UTC),
            trigger=trigger,
            records=1,
            linked=1,
            by_level={MatchLevel.HIGH: 1},
        )


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
