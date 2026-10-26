from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.domain.enums import Outcome
from credentialing.domain.models import SourceSnapshot
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
