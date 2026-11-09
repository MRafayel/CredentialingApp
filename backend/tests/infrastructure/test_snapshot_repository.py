from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.domain.enums import DatasetLoadMode, Outcome
from credentialing.domain.models import DatasetLoad, SourceSnapshot
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.snapshots import PostgresSnapshotRepository
from tests.synthetic import NOW

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _snapshot(source_id: str, hours_ago: int, outcome: Outcome) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=source_id,
        fetched_at=NOW - timedelta(hours=hours_ago),
        outcome=outcome,
        source_url="https://example.test/list.tsv",
        content_hash="ab" * 32 if outcome is Outcome.COMPLETED else None,
        etag='"v1"',
        last_modified=NOW - timedelta(days=3),
        byte_size=1024,
        row_count=3,
        note=None if outcome is Outcome.COMPLETED else "synthetic outage",
    )


async def test_latest_returns_the_most_recent_snapshot_of_that_source(
    database: ConnectionSource,
) -> None:
    repository = PostgresSnapshotRepository(database)
    older = _snapshot("test_exclusions", 48, Outcome.COMPLETED)
    newest = _snapshot("test_exclusions", 1, Outcome.SOURCE_UNAVAILABLE)
    other_source = _snapshot("test_other_list", 0, Outcome.COMPLETED)
    for snapshot in (older, newest, other_source):
        await repository.add(snapshot)

    assert await repository.latest("test_exclusions") == newest


async def test_latest_is_none_for_a_source_never_fetched(database: ConnectionSource) -> None:
    assert await PostgresSnapshotRepository(database).latest("test_never_fetched") is None


async def test_latest_completed_skips_later_failures(database: ConnectionSource) -> None:
    repository = PostgresSnapshotRepository(database)
    completed = _snapshot("test_exclusions", 48, Outcome.COMPLETED)
    failed = _snapshot("test_exclusions", 1, Outcome.BLOCKED)
    for snapshot in (completed, failed):
        await repository.add(snapshot)

    assert await repository.latest_completed("test_exclusions") == completed
    assert await repository.latest_completed("test_never_fetched") is None


async def test_a_load_is_stored_on_its_snapshot_and_read_back(database: ConnectionSource) -> None:
    repository = PostgresSnapshotRepository(database)
    loaded = _snapshot("test_dataset", 30, Outcome.COMPLETED)
    unchanged = _snapshot("test_dataset", 2, Outcome.COMPLETED)
    await repository.add(loaded)
    await repository.add(unchanged)
    load = DatasetLoad(
        mode=DatasetLoadMode.FULL,
        scanned=9,
        added=4,
        changed=1,
        unchanged=4,
        missing=2,
        phases={"download": timedelta(seconds=31.5), "scan": timedelta(seconds=2)},
        cpu_time=timedelta(seconds=12.25),
    )

    await repository.set_load(loaded.id, load)

    found = await repository.latest_loaded("test_dataset")
    assert found is not None
    assert found.id == loaded.id
    assert found.load == load
    assert list(found.load.phases) == ["download", "scan"]
    assert found.row_count == 9
    latest = await repository.latest_completed("test_dataset")
    assert latest is not None
    assert latest.id == unchanged.id
    assert latest.load is None
    assert await repository.latest_loaded("test_never_loaded") is None


async def test_count_since_counts_a_source_requests_and_its_failures(
    database: ConnectionSource,
) -> None:
    repository = PostgresSnapshotRepository(database)
    for snapshot in (
        _snapshot("test_on_demand", 200, Outcome.COMPLETED),
        _snapshot("test_on_demand", 5, Outcome.COMPLETED),
        _snapshot("test_on_demand", 3, Outcome.BLOCKED),
        _snapshot("test_on_demand", 1, Outcome.COMPLETED),
        _snapshot("test_other_list", 1, Outcome.FAILED),
    ):
        await repository.add(snapshot)

    assert await repository.count_since("test_on_demand", NOW - timedelta(days=7)) == (3, 1)
    assert await repository.count_since("test_on_demand", NOW - timedelta(hours=3)) == (2, 1)
    assert await repository.count_since("test_never_fetched", NOW - timedelta(days=7)) == (0, 0)
