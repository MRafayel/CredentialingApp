"""Shared fixtures."""

from collections.abc import AsyncIterator

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import PoolTimeout

from credentialing.infrastructure.config import get_settings
from credentialing.infrastructure.database import create_pool


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
