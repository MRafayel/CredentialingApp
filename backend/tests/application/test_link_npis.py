"""Linking exclusion records to NPIs over in-memory storage, and the syncs that trigger it."""

from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.application.link_npis import (
    MIRROR_NOT_LOADED,
    NPPES_LOAD_TRIGGER,
    LinkNpis,
    latest_mirror_load,
    list_sync_trigger,
)
from credentialing.application.sync_source import SyncSource
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import DatasetLoadMode, MatchLevel, Outcome
from credentialing.domain.models import (
    Address,
    DatasetLoad,
    ExclusionRecord,
    FetchResult,
    NpiRecord,
    SourceSnapshot,
)
from credentialing.domain.ports import DatasetFetch
from tests.fakes import (
    FakeLinker,
    FixedClock,
    InMemoryNpiLinks,
    InMemoryNpiRegistry,
    InMemoryRecords,
    InMemorySnapshots,
    ListDataset,
    ScriptedConnector,
    ScriptedDatasetConnector,
    capabilities,
    dataset_capabilities,
)
from tests.synthetic import NOW, exclusion, make_npi, npi_person

pytestmark = pytest.mark.anyio

LIST = "fake_state"
OTHER_LIST = "fake_federal"
MONTHLY = "fake_monthly"
WEEKLY = "fake_weekly"
NPI = make_npi("140000001")
SAMPLEVILLE = Address(city="Sampleville", state="NY", postal_code="99901")


def _snapshot(source_id: str, *, hours: int = 0, loaded: bool = False) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=source_id,
        fetched_at=NOW + timedelta(hours=hours),
        outcome=Outcome.COMPLETED,
        source_url=f"https://example.test/{source_id}",
        content_hash=uuid4().hex,
        load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=1, added=1) if loaded else None,
    )


class _World:
    def __init__(self, *mirror: NpiRecord) -> None:
        self.directory = ConnectorRegistry()
        self.directory.register(ScriptedConnector(capabilities(LIST, frozenset({"NY"}))))
        self.directory.register(ScriptedConnector(capabilities(OTHER_LIST)))
        self.directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.directory.register(ScriptedDatasetConnector(dataset_capabilities(WEEKLY)))
        self.snapshots = InMemorySnapshots()
        self.records = InMemoryRecords()
        self.registry = InMemoryNpiRegistry(*mirror)
        self.links = InMemoryNpiLinks(self.records, self.registry)
        self.clock = FixedClock(NOW)
        self.use_case = LinkNpis(
            self.directory, self.snapshots, self.links, self.clock, batch_size=2
        )

    async def load_mirror(self, source_id: str = MONTHLY, *, hours: int = 0) -> SourceSnapshot:
        snapshot = _snapshot(source_id, hours=hours, loaded=True)
        await self.snapshots.add(snapshot)
        return snapshot

    async def list(self, *records: ExclusionRecord, source_id: str = LIST) -> None:
        snapshot = _snapshot(source_id)
        await self.snapshots.add(snapshot)
        own = [
            r.model_copy(update={"snapshot_id": snapshot.id, "source_id": source_id})
            for r in records
        ]
        await self.records.replace_current(source_id, snapshot.id, own, NOW, source_changed=True)


async def test_the_mirror_is_the_newest_load_across_the_dataset_sources() -> None:
    world = _World()
    monthly = await world.load_mirror(MONTHLY, hours=0)
    weekly = await world.load_mirror(WEEKLY, hours=5)
    await world.snapshots.add(_snapshot(MONTHLY, hours=9))  # an unchanged poll loads nothing

    assert await latest_mirror_load(world.directory, world.snapshots) == weekly
    assert monthly != weekly


async def test_without_a_loaded_mirror_nothing_is_linked_and_the_run_says_so() -> None:
    world = _World(npi_person(NPI))
    await world.list(exclusion())

    run = await world.use_case.run(source_id=None, trigger="cli")

    assert (run.records, run.linked, run.note) == (0, 0, MIRROR_NOT_LOADED)
    assert run.mirror_snapshot_id is None
    assert world.links.links == {}
    assert world.links.runs == [run]


async def test_a_run_links_every_stale_record_in_batches_and_measures_itself() -> None:
    world = _World(
        npi_person(NPI, practice=SAMPLEVILLE),
        npi_person(make_npi("140000002"), first="RILEY"),
    )
    mirror = await world.load_mirror()
    person = exclusion().model_copy(update={"address": SAMPLEVILLE})
    riley = exclusion(first="Riley")
    nobody = exclusion(last="Nobody")
    await world.list(person, riley, nobody)

    run = await world.use_case.run(source_id=None, trigger="cli")

    assert (run.records, run.linked, run.mirror_snapshot_id) == (3, 2, mirror.id)
    assert run.by_level == {
        MatchLevel.EXACT: 0,
        MatchLevel.HIGH: 1,
        MatchLevel.POTENTIAL: 1,
        MatchLevel.AMBIGUOUS: 0,
        MatchLevel.NONE: 1,
    }
    assert list(run.phases) == ["candidates", "scoring", "writing"]
    assert run.cpu_time is not None
    assert [len(batch) for batch in world.links.asked] == [2, 1]
    (link,) = world.links.links[person.id]
    assert (link.npi, link.level, link.mirror_snapshot_id) == (NPI, MatchLevel.HIGH, mirror.id)
    (none,) = world.links.links[nobody.id]
    assert none.npi is None
    assert none.match.explanation == "No match: no candidate with this name."
    assert world.links.runs == [run]


async def test_only_stale_records_are_relinked() -> None:
    world = _World(npi_person(NPI))
    await world.load_mirror()
    await world.list(exclusion())
    await world.use_case.run(source_id=None, trigger="cli")

    unchanged = await world.use_case.run(source_id=None, trigger="cli")
    await world.load_mirror(WEEKLY, hours=1)
    after_a_load = await world.use_case.run(source_id=None, trigger=NPPES_LOAD_TRIGGER)

    assert unchanged.records == 0
    assert after_a_load.records == 1


async def test_a_list_sync_relinks_only_that_list() -> None:
    world = _World(npi_person(NPI))
    await world.load_mirror()
    await world.list(exclusion(), source_id=LIST)
    await world.list(exclusion(last="Otherperson"), source_id=OTHER_LIST)

    run = await world.use_case.run(source_id=LIST, trigger=list_sync_trigger(LIST))

    assert run.records == 1
    assert run.trigger == "list_sync:fake_state"
    assert {r.source_id for r in world.records.all.values() if r.id in world.links.links} == {LIST}


async def test_a_failure_while_writing_is_raised_and_no_run_is_stored() -> None:
    world = _World(npi_person(NPI))
    await world.load_mirror()
    await world.list(exclusion())
    world.links.fail = RuntimeError("synthetic write failure")

    with pytest.raises(RuntimeError):
        await world.use_case.run(source_id=None, trigger="cli")

    assert world.links.runs == []


async def test_the_summary_comes_from_the_links() -> None:
    world = _World(npi_person(NPI))
    await world.load_mirror()
    await world.list(exclusion(), exclusion(last="Nobody"))
    run = await world.use_case.run(source_id=None, trigger="cli")

    summary = await world.use_case.summary()

    assert summary.records == 2
    assert dict(summary.by_best_level) == {MatchLevel.POTENTIAL: 1, MatchLevel.NONE: 1}
    assert summary.last_run == run


# --- The syncs that trigger linking ---------------------------------------------------------


def _list_sync(linker: FakeLinker, *results: FetchResult) -> tuple[SyncSource, InMemorySnapshots]:
    directory = ConnectorRegistry()
    directory.register(ScriptedConnector(capabilities(LIST), *results))
    snapshots = InMemorySnapshots()
    sync = SyncSource(directory, snapshots, InMemoryRecords(), FixedClock(NOW), link_npis=linker)
    return sync, snapshots


def _fetched(*lasts: str, hours: int = 0) -> FetchResult:
    snapshot = _snapshot(LIST, hours=hours)
    records = tuple(exclusion(source_id=LIST, snapshot_id=snapshot.id, last=n) for n in lasts)
    return FetchResult(snapshot=snapshot, records=records)


async def test_a_list_sync_that_changed_rows_relinks_that_list() -> None:
    linker = FakeLinker()
    sync, _ = _list_sync(linker, _fetched("Testperson"), _fetched("Testperson", hours=1))

    first = await sync.run(LIST)
    unchanged = await sync.run(LIST)

    assert linker.calls == [(LIST, "list_sync:fake_state")]
    assert first.linking is not None
    assert unchanged.linking is None


async def test_a_sync_without_a_new_list_does_not_relink() -> None:
    linker = FakeLinker()
    sync, _ = _list_sync(linker, FetchResult(snapshot=_snapshot(LIST)))

    report = await sync.run(LIST)

    assert linker.calls == []
    assert report.linking is None


async def test_a_linking_failure_is_reported_and_the_sync_stands() -> None:
    linker = FakeLinker(fail=RuntimeError("synthetic linking failure"))
    fetched = _fetched("Testperson")
    sync, snapshots = _list_sync(linker, fetched)

    report = await sync.run(LIST)

    assert report.linking is None
    assert report.linking_failed == "NPI linking failed (RuntimeError)"
    assert report.diff is not None
    assert report.diff.added == 1
    assert [s.id for s in snapshots.stored] == [fetched.snapshot.id]


async def test_a_dataset_load_that_changed_rows_relinks_everything() -> None:
    linker = FakeLinker()
    directory = ConnectorRegistry()
    first, second = _snapshot(MONTHLY), _snapshot(MONTHLY, hours=1)
    record = npi_person(NPI)
    directory.register(
        ScriptedDatasetConnector(
            dataset_capabilities(MONTHLY),
            DatasetFetch(snapshot=first, mode=DatasetLoadMode.FULL, dataset=ListDataset(record)),
            DatasetFetch(snapshot=second, mode=DatasetLoadMode.FULL, dataset=ListDataset(record)),
        )
    )
    sync = SyncSource(
        directory,
        InMemorySnapshots(),
        InMemoryRecords(),
        FixedClock(NOW),
        npi_registry=InMemoryNpiRegistry(),
        link_npis=linker,
    )

    loaded = await sync.run(MONTHLY)
    unchanged = await sync.run(MONTHLY)

    assert linker.calls == [(None, "nppes_load")]
    assert loaded.linking is not None
    assert unchanged.load is not None
    assert unchanged.linking is None
