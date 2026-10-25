"""Source snapshots in Postgres (shared reference data)."""

from typing import Final

from psycopg import sql
from psycopg.rows import class_row

from credentialing.domain.models import SourceSnapshot
from credentialing.domain.types import SourceId
from credentialing.infrastructure.database import ConnectionSource

_SELECT: Final = sql.SQL(
    "select id, source_id, fetched_at, outcome, source_url, content_hash, etag, last_modified,"
    " byte_size, row_count, note from source_snapshots"
)


class PostgresSnapshotRepository:
    """Implements :class:`~credentialing.domain.ports.SnapshotRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def add(self, snapshot: SourceSnapshot) -> None:
        """Store a new snapshot."""
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                insert into source_snapshots (
                    id, source_id, fetched_at, outcome, source_url, content_hash, etag,
                    last_modified, byte_size, row_count, note
                ) values (
                    {snapshot.id}, {snapshot.source_id}, {snapshot.fetched_at},
                    {snapshot.outcome.value}, {snapshot.source_url}, {snapshot.content_hash},
                    {snapshot.etag}, {snapshot.last_modified}, {snapshot.byte_size},
                    {snapshot.row_count}, {snapshot.note}
                )
                """
            )

    async def latest(self, source_id: SourceId) -> SourceSnapshot | None:
        """Return the source's most recent snapshot, whatever its outcome."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(SourceSnapshot)) as cursor,
        ):
            await cursor.execute(
                t"{_SELECT:q} where source_id = {source_id} order by fetched_at desc, id limit 1"
            )
            return await cursor.fetchone()
