"""Shared fixtures."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import PoolTimeout

from credentialing.infrastructure.config import get_settings
from credentialing.infrastructure.database import ConnectionSource, create_pool

_SCHEMA_TABLES = (
    "organizations",
    "providers",
    "source_snapshots",
    "exclusion_records",
    "verifications",
)


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` tests on asyncio only."""
    return "asyncio"


@pytest.fixture
async def db_connection() -> AsyncIterator[AsyncConnection[TupleRow]]:
    """A connection to the local database whose changes are rolled back after the test.

    Integration tests can write freely without leaving anything behind in the development
    database.
    """
    pool = create_pool(get_settings().database_url.get_secret_value(), max_size=1)
    try:
        await pool.open(wait=True, timeout=3)
    except PoolTimeout:
        await pool.close()
        pytest.fail("The local database is not running. Start it with `make db-start`.")
    try:
        async with pool.connection() as connection, connection.transaction(force_rollback=True):
            yield connection
    finally:
        await pool.close()


class _SingleConnection:
    """Lends the test's one rolled-back connection wherever a repository expects a pool."""

    def __init__(self, connection: AsyncConnection[TupleRow]) -> None:
        self._connection = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[TupleRow]]:
        yield self._connection


@pytest.fixture
async def database(db_connection: AsyncConnection[TupleRow]) -> ConnectionSource:
    """What repositories take in place of the pool, inside ``db_connection``'s transaction.

    A repository's own transactions become savepoints, so everything is still rolled back.
    """
    cursor = await db_connection.execute(
        t"select table_name from unnest({list(_SCHEMA_TABLES)}::text[]) as table_name"
        t" where to_regclass('public.' || table_name) is null"
    )
    missing = [row[0] for row in await cursor.fetchall()]
    if missing:
        pytest.fail(
            f"The local database schema is missing tables ({', '.join(missing)}). "
            "Rebuild it from the migrations with `make db-reset`."
        )
    return _SingleConnection(db_connection)
