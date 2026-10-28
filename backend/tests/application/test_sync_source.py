from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.errors import UnknownSourceError
from credentialing.application.sync_source import (
    DUE_TOLERANCE,
    SHRINK_GUARD_MIN_ROWS,
    SyncSource,
)
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import Outcome
from credentialing.domain.models import (
    ExclusionRecord,
    FetchResult,
    SnapshotDiff,
    SourceSnapshot,
)
from tests.fakes import FixedClock, InMemoryRecords, InMemorySnapshots, ScriptedConnector
from tests.fakes import capabilities as make_capabilities
from tests.synthetic import NOW, exclusion

pytestmark = pytest.mark.anyio

SOURCE = "fake_federal"


def _snapshot(
    outcome: Outcome = Outcome.COMPLETED, *, hours: int = 0, rows: int | None = None
) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW + timedelta(hours=hours),
        outcome=outcome,
        source_url="https://example.test/list",
        content_hash="ab" * 32 if outcome is Outcome.COMPLETED else None,
        row_count=rows,
        note=None if outcome is Outcome.COMPLETED else "synthetic failure",
    )


def _loaded(snapshot: SourceSnapshot, *lasts: str) -> FetchResult:
    records = tuple(exclusion(source_id=SOURCE, snapshot_id=snapshot.id, last=n) for n in lasts)
    return FetchResult(snapshot=snapshot, records=records)


def _use_case(
    connector: ScriptedConnector,
    snapshots: InMemorySnapshots,
    records: InMemoryRecords,
) -> SyncSource:
    registry = ConnectorRegistry()
    registry.register(connector)
    return SyncSource(registry, snapshots, records, FixedClock(NOW))


async def test_a_new_list_is_stored_and_diffed() -> None:
    first = _snapshot()
    connector = ScriptedConnector(make_capabilities(SOURCE), _loaded(first, "Alpha", "Beta"))
    snapshots, records = InMemorySnapshots(), InMemoryRecords()

    report = await _use_case(connector, snapshots, records).run(SOURCE)

    assert report.snapshot == first
    assert report.diff == SnapshotDiff(added=2, removed=0, changed=0, unchanged=0)
    assert snapshots.stored == [first]
    assert connector.previous_seen == [None]


async def test_an_unchanged_fetch_stores_a_snapshot_and_no_diff() -> None:
    first, unchanged = _snapshot(), _snapshot(hours=1)
    connector = ScriptedConnector(
        make_capabilities(SOURCE), _loaded(first, "Alpha"), FetchResult(snapshot=unchanged)
    )
    snapshots, records = InMemorySnapshots(), InMemoryRecords()
    use_case = _use_case(connector, snapshots, records)
    await use_case.run(SOURCE)

    report = await use_case.run(SOURCE)

    assert report.diff is None
    assert snapshots.stored == [first, unchanged]
    assert connector.previous_seen == [None, first]
    assert len(records.current) == 1


async def test_a_blocked_fetch_is_stored_and_leaves_the_records_alone() -> None:
    first, blocked = _snapshot(), _snapshot(Outcome.BLOCKED, hours=1)
    connector = ScriptedConnector(
        make_capabilities(SOURCE), _loaded(first, "Alpha"), FetchResult(snapshot=blocked)
    )
    snapshots, records = InMemorySnapshots(), InMemoryRecords()
    use_case = _use_case(connector, snapshots, records)
    await use_case.run(SOURCE)

    report = await use_case.run(SOURCE)

    assert report.snapshot.outcome is Outcome.BLOCKED
    assert report.diff is None
    assert await snapshots.latest(SOURCE) == blocked
    assert len(records.current) == 1


async def test_force_ignores_the_previous_snapshot() -> None:
    first, again = _snapshot(), _snapshot(hours=1)
    connector = ScriptedConnector(
        make_capabilities(SOURCE), _loaded(first, "Alpha"), _loaded(again, "Alpha")
    )
    use_case = _use_case(connector, InMemorySnapshots(), InMemoryRecords())
    await use_case.run(SOURCE)

    report = await use_case.run(SOURCE, force=True)

    assert connector.previous_seen == [None, None]
    assert report.diff == SnapshotDiff(added=0, removed=0, changed=0, unchanged=1)


async def test_unknown_source() -> None:
    connector = ScriptedConnector(make_capabilities(SOURCE))

    with pytest.raises(UnknownSourceError, match="fake_unknown"):
        await _use_case(connector, InMemorySnapshots(), InMemoryRecords()).run("fake_unknown")


class _BrokenRecords(InMemoryRecords):
    async def replace_current(
        self,
        source_id: str,
        snapshot_id: UUID,
        records: Sequence[ExclusionRecord],
        now: datetime,
    ) -> SnapshotDiff:
        raise ConnectionError("database went away")


async def test_a_storage_failure_marks_the_source_for_a_fresh_download() -> None:
    first = _snapshot()
    connector = ScriptedConnector(make_capabilities(SOURCE), _loaded(first, "Alpha"))
    snapshots = InMemorySnapshots()

    with pytest.raises(ConnectionError):
        await _use_case(connector, snapshots, _BrokenRecords()).run(SOURCE)

    latest = await snapshots.latest(SOURCE)
    assert latest is not None
    assert latest.outcome is Outcome.FAILED
    assert latest.content_hash is None
    assert latest.fetched_at > first.fetched_at


def _many(snapshot: SourceSnapshot, count: int) -> FetchResult:
    loaded = _loaded(snapshot, *(f"Person{index:04d}" for index in range(count)))
    return FetchResult(
        snapshot=snapshot.model_copy(update={"row_count": count}), records=loaded.records
    )


async def test_a_list_that_shrank_by_half_is_refused() -> None:
    before = SHRINK_GUARD_MIN_ROWS * 2
    first, shrunk = _snapshot(), _snapshot(hours=1)
    connector = ScriptedConnector(
        make_capabilities(SOURCE), _many(first, before), _many(shrunk, before // 2 - 1)
    )
    snapshots, records = InMemorySnapshots(), InMemoryRecords()
    use_case = _use_case(connector, snapshots, records)
    await use_case.run(SOURCE)

    report = await use_case.run(SOURCE)

    assert report.diff is None
    assert report.snapshot.outcome is Outcome.FAILED
    assert report.snapshot.note == (
        f"row count dropped from {before} to {before // 2 - 1}; refusing to apply without --force"
    )
    assert report.snapshot.content_hash is None
    assert snapshots.stored[-1] == report.snapshot
    assert len(records.current) == before


async def test_force_applies_a_shrunk_list() -> None:
    before = SHRINK_GUARD_MIN_ROWS * 2
    first, shrunk = _snapshot(), _snapshot(hours=1)
    connector = ScriptedConnector(
        make_capabilities(SOURCE), _many(first, before), _many(shrunk, 10)
    )
    records = InMemoryRecords()
    use_case = _use_case(connector, InMemorySnapshots(), records)
    await use_case.run(SOURCE)

    report = await use_case.run(SOURCE, force=True)

    assert report.snapshot.outcome is Outcome.COMPLETED
    assert report.diff == SnapshotDiff(added=0, removed=before - 10, changed=0, unchanged=10)


@pytest.mark.parametrize(("before", "after"), [(SHRINK_GUARD_MIN_ROWS - 1, 1), (200, 100)])
async def test_small_lists_and_moderate_drops_are_applied(before: int, after: int) -> None:
    first, second = _snapshot(), _snapshot(hours=1)
    connector = ScriptedConnector(
        make_capabilities(SOURCE), _many(first, before), _many(second, after)
    )
    use_case = _use_case(connector, InMemorySnapshots(), InMemoryRecords())
    await use_case.run(SOURCE)

    report = await use_case.run(SOURCE)

    assert report.snapshot.outcome is Outcome.COMPLETED
    assert report.diff is not None
    assert report.diff.removed == before - after


async def test_a_source_never_completed_is_due_now() -> None:
    snapshots = InMemorySnapshots()
    await snapshots.add(_snapshot(Outcome.BLOCKED))
    use_case = _use_case(ScriptedConnector(make_capabilities(SOURCE)), snapshots, InMemoryRecords())

    assert await use_case.next_due(SOURCE) is None


async def test_the_next_poll_is_due_one_interval_after_the_last_completed_fetch() -> None:
    snapshots = InMemorySnapshots()
    await snapshots.add(_snapshot(hours=-48))
    await snapshots.add(_snapshot(Outcome.SOURCE_UNAVAILABLE, hours=-1))
    capabilities = make_capabilities(SOURCE)
    use_case = _use_case(ScriptedConnector(capabilities), snapshots, InMemoryRecords())

    due = await use_case.next_due(SOURCE)

    assert due == NOW - timedelta(hours=48) + capabilities.poll_interval - DUE_TOLERANCE


async def test_a_daily_source_fetched_yesterday_a_little_later_is_due_again() -> None:
    snapshots = InMemorySnapshots()
    await snapshots.add(
        _snapshot(hours=-24).model_copy(
            update={"fetched_at": NOW - timedelta(hours=23, minutes=59)}
        )
    )
    capabilities = make_capabilities(SOURCE).model_copy(update={"poll_interval": timedelta(days=1)})
    use_case = _use_case(ScriptedConnector(capabilities), snapshots, InMemoryRecords())

    due = await use_case.next_due(SOURCE)

    assert due is not None
    assert due <= NOW


async def test_next_due_of_an_unknown_source() -> None:
    use_case = _use_case(
        ScriptedConnector(make_capabilities(SOURCE)), InMemorySnapshots(), InMemoryRecords()
    )

    with pytest.raises(UnknownSourceError, match="fake_unknown"):
        await use_case.next_due("fake_unknown")
