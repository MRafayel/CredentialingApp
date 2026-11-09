"""Source snapshots in Postgres (shared reference data)."""

from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg import sql
from psycopg.rows import class_row

from credentialing.domain.enums import Outcome
from credentialing.domain.models import DatasetLoad, SourceSnapshot
from credentialing.domain.types import SourceId
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import model_json

_SELECT: Final = sql.SQL(
    "select id, source_id, fetched_at, outcome, source_url, content_hash, etag, last_modified,"
    " byte_size, row_count, note, load from source_snapshots"
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
                    last_modified, byte_size, row_count, note, load
                ) values (
                    {snapshot.id}, {snapshot.source_id}, {snapshot.fetched_at},
                    {snapshot.outcome.value}, {snapshot.source_url}, {snapshot.content_hash},
                    {snapshot.etag}, {snapshot.last_modified}, {snapshot.byte_size},
                    {snapshot.row_count}, {snapshot.note},
                    {model_json(snapshot.load) if snapshot.load else None}
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

    async def latest_completed(self, source_id: SourceId) -> SourceSnapshot | None:
        """Return the source's most recent ``COMPLETED`` snapshot: the list checks rely on."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(SourceSnapshot)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q} where source_id = {source_id} and outcome = {Outcome.COMPLETED.value}
                order by fetched_at desc, id limit 1
                """
            )
            return await cursor.fetchone()

    async def latest_loaded(self, source_id: SourceId) -> SourceSnapshot | None:
        """Return the source's most recent snapshot whose dataset was loaded (``load`` set)."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(SourceSnapshot)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q} where source_id = {source_id} and load is not null
                order by fetched_at desc, id limit 1
                """
            )
            return await cursor.fetchone()

    async def set_load(self, snapshot_id: UUID, load: DatasetLoad) -> None:
        """Record what loading the snapshot's dataset did; ``row_count`` becomes
        ``load.scanned``."""
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                update source_snapshots set load = {model_json(load)}, row_count = {load.scanned}
                where id = {snapshot_id}
                """
            )

    async def count_since(self, source_id: SourceId, since: datetime) -> tuple[int, int]:
        """How many snapshots the source has fetched at or after ``since``, and how many of
        them did not complete; one index range scan."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select count(*), count(*) filter (where outcome <> {Outcome.COMPLETED.value})
                from source_snapshots where source_id = {source_id} and fetched_at >= {since}
                """
            )
            row = await cursor.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)
