from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.application.source_status import SourceHealth, SourceStatusReport
from credentialing.application.sync_source import DUE_TOLERANCE, SyncSource
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import DatasetLoadMode, EntityKind, NpiStatus, Outcome
from credentialing.domain.models import DatasetLoad, NpiRecord, PersonName, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from credentialing.domain.ports import DatasetFetch
from tests.fakes import (
    FixedClock,
    InMemoryNpiRegistry,
    InMemoryRecords,
    InMemorySnapshots,
    ListDataset,
    ScriptedConnector,
    ScriptedDatasetConnector,
    dataset_capabilities,
)
from tests.fakes import capabilities as list_capabilities
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = pytest.mark.anyio

SOURCE = "fake_dataset"


def _snapshot(
    outcome: Outcome = Outcome.COMPLETED, *, hours: int = 0, load: DatasetLoad | None = None
) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW + timedelta(hours=hours),
        outcome=outcome,
        source_url="https://example.test/files/registry.zip",
        content_hash=uuid4().hex * 2 if outcome is Outcome.COMPLETED else None,
        note=None if outcome is Outcome.COMPLETED else "synthetic failure",
        load=load,
    )


def _record(number: str, line_hash: str = "ab" * 32) -> NpiRecord:
    return NpiRecord(
        npi=make_npi(number),
        entity_kind=EntityKind.INDIVIDUAL,
        status=NpiStatus.ACTIVE,
        person_name=PersonName(last="Testperson", first="Avery"),
        line_hash=line_hash,
        snapshot_id=uuid4(),
    )


def _fetch(
    snapshot: SourceSnapshot,
    dataset: ListDataset | None,
    mode: DatasetLoadMode = DatasetLoadMode.FULL,
) -> DatasetFetch:
    return DatasetFetch(snapshot=snapshot, mode=mode, dataset=dataset)


def _use_case(
    connector: ScriptedDatasetConnector,
    snapshots: InMemorySnapshots,
    registry: InMemoryNpiRegistry | None,
) -> SyncSource:
    directory = ConnectorRegistry()
    directory.register(connector)
    return SyncSource(
        directory, snapshots, InMemoryRecords(), FixedClock(NOW), npi_registry=registry
    )


async def test_a_new_file_is_loaded_and_its_load_stored_with_timings() -> None:
    snapshot = _snapshot()
    dataset = ListDataset(_record("170000001"), _record("170000002"))
    connector = ScriptedDatasetConnector(dataset_capabilities(SOURCE), _fetch(snapshot, dataset))
    snapshots, registry = InMemorySnapshots(), InMemoryNpiRegistry()

    report = await _use_case(connector, snapshots, registry).run(SOURCE)

    assert report.load is not None
    assert (report.load.scanned, report.load.added) == (2, 2)
    assert list(report.load.phases) == ["download", "scan", "apply"]
    assert report.load.cpu_time is not None
    assert report.load.cpu_time >= timedelta(0)
    assert report.snapshot.load == report.load
    assert report.snapshot.row_count == 2
    assert report.diff is None
    (stored,) = snapshots.stored
    assert stored.id == snapshot.id
    assert stored.load == report.load
    assert registry.loads == [(DatasetLoadMode.FULL, snapshot.id, None)]
    assert dataset.closed
    assert await registry.count() == 2


async def test_an_unchanged_or_failed_fetch_loads_nothing() -> None:
    unchanged = _snapshot()
    failed = _snapshot(Outcome.FAILED, hours=1)
    connector = ScriptedDatasetConnector(
        dataset_capabilities(SOURCE), _fetch(unchanged, None), _fetch(failed, None)
    )
    snapshots, registry = InMemorySnapshots(), InMemoryNpiRegistry()
    use_case = _use_case(connector, snapshots, registry)

    first = await use_case.run(SOURCE)
    second = await use_case.run(SOURCE)

    assert (first.load, second.load) == (None, None)
    assert snapshots.stored == [unchanged, failed]
    assert registry.loads == []
    assert connector.previous_seen == [None, unchanged]


async def test_force_hands_no_previous_snapshot_to_the_connector() -> None:
    connector = ScriptedDatasetConnector(
        dataset_capabilities(SOURCE), _fetch(_snapshot(), None), _fetch(_snapshot(hours=1), None)
    )
    use_case = _use_case(connector, InMemorySnapshots(), InMemoryNpiRegistry())

    await use_case.run(SOURCE)
    await use_case.run(SOURCE, force=True)

    assert connector.previous_seen == [None, None]


async def test_a_failed_load_is_followed_by_a_failed_snapshot_and_re_raised() -> None:
    snapshot = _snapshot()
    dataset = ListDataset(_record("170000001"))
    connector = ScriptedDatasetConnector(dataset_capabilities(SOURCE), _fetch(snapshot, dataset))
    snapshots = InMemorySnapshots()
    registry = InMemoryNpiRegistry(fail=RuntimeError("database went away"))

    with pytest.raises(RuntimeError):
        await _use_case(connector, snapshots, registry).run(SOURCE)

    assert [s.outcome for s in snapshots.stored] == [Outcome.COMPLETED, Outcome.FAILED]
    assert snapshots.stored[1].note == f"records of snapshot {snapshot.id} could not be stored"
    assert snapshots.stored[0].load is None
    assert dataset.closed


def _loaded(scanned: int, mode: DatasetLoadMode = DatasetLoadMode.FULL) -> SourceSnapshot:
    return _snapshot(hours=-48, load=DatasetLoad(mode=mode, scanned=scanned, added=scanned))


async def test_a_full_file_with_half_the_rows_of_the_last_load_is_refused() -> None:
    snapshots = InMemorySnapshots()
    snapshots.stored.append(_loaded(200_000))
    dataset = ListDataset(_record("170000001"), _record("170000002"))
    connector = ScriptedDatasetConnector(dataset_capabilities(SOURCE), _fetch(_snapshot(), dataset))
    registry = InMemoryNpiRegistry()

    report = await _use_case(connector, snapshots, registry).run(SOURCE)

    assert report.snapshot.outcome is Outcome.FAILED
    assert report.snapshot.note == (
        "row count dropped from 200000 to 2; refusing to apply without --force"
    )
    assert report.load is None
    assert registry.loads[0][2] == 100_000
    assert snapshots.stored[-1] == report.snapshot
    assert dataset.closed
    assert await registry.count() == 0


async def test_force_and_other_modes_skip_the_shrink_guard() -> None:
    for mode, force in ((DatasetLoadMode.FULL, True), (DatasetLoadMode.INCREMENTAL, False)):
        snapshots = InMemorySnapshots()
        snapshots.stored.append(_loaded(200_000))
        dataset = ListDataset(_record("170000001"))
        connector = ScriptedDatasetConnector(
            dataset_capabilities(SOURCE), _fetch(_snapshot(), dataset, mode)
        )
        registry = InMemoryNpiRegistry()

        report = await _use_case(connector, snapshots, registry).run(SOURCE, force=force)

        assert report.load is not None
        assert registry.loads[0][2] is None


async def test_a_small_last_load_sets_no_guard() -> None:
    snapshots = InMemorySnapshots()
    snapshots.stored.append(_loaded(99_999))
    connector = ScriptedDatasetConnector(
        dataset_capabilities(SOURCE), _fetch(_snapshot(), ListDataset(_record("170000001")))
    )
    registry = InMemoryNpiRegistry()

    await _use_case(connector, snapshots, registry).run(SOURCE)

    assert registry.loads[0][2] is None


async def test_a_dataset_source_without_a_mirror_is_a_wiring_error() -> None:
    connector = ScriptedDatasetConnector(dataset_capabilities(SOURCE), _fetch(_snapshot(), None))

    with pytest.raises(TypeError, match="npi_registry"):
        await _use_case(connector, InMemorySnapshots(), None).run(SOURCE)


async def test_a_dataset_source_is_due_one_poll_interval_after_its_last_completed_fetch() -> None:
    snapshots = InMemorySnapshots()
    use_case = _use_case(
        ScriptedDatasetConnector(dataset_capabilities(SOURCE)), snapshots, InMemoryNpiRegistry()
    )

    assert await use_case.next_due(SOURCE) is None
    snapshots.stored.append(_snapshot())
    assert await use_case.next_due(SOURCE) == NOW + timedelta(days=1) - DUE_TOLERANCE


async def test_the_report_shows_dataset_sources_with_the_mirror_count_and_last_load() -> None:
    directory = ConnectorRegistry()
    directory.register(ScriptedDatasetConnector(dataset_capabilities(SOURCE)))
    directory.register(ScriptedConnector(list_capabilities("fake_list")))
    load = DatasetLoad(
        mode=DatasetLoadMode.FULL, scanned=3, added=3, phases={"scan": timedelta(seconds=1)}
    )
    snapshots = InMemorySnapshots()
    snapshots.stored += [_snapshot(hours=-2, load=load), _snapshot(hours=-1)]
    records = InMemoryRecords()
    await records.replace_current(
        "fake_list",
        uuid4(),
        [exclusion(source_id="fake_list")],
        NOW,
        source_changed=True,
    )
    registry = InMemoryNpiRegistry(_record("170000001"), _record("170000002"))
    report = SourceStatusReport(
        directory,
        snapshots,
        records,
        DEFAULT_FRESHNESS,
        FixedClock(NOW),
        npi_registry=registry,
    )

    dataset_status, list_status = await report.run()

    assert dataset_status.source_id == SOURCE
    assert dataset_status.current_records == 2
    assert dataset_status.last_load == load
    assert dataset_status.health is SourceHealth.OK
    assert dataset_status.freshness_max_age == timedelta(days=30)
    assert list_status.source_id == "fake_list"
    assert list_status.current_records == 1
    assert list_status.last_load is None


async def test_a_report_over_dataset_sources_without_a_mirror_is_a_wiring_error() -> None:
    directory = ConnectorRegistry()
    directory.register(ScriptedDatasetConnector(dataset_capabilities(SOURCE)))
    report = SourceStatusReport(
        directory, InMemorySnapshots(), InMemoryRecords(), DEFAULT_FRESHNESS, FixedClock(NOW)
    )

    with pytest.raises(TypeError, match="npi_registry"):
        await report.run()
