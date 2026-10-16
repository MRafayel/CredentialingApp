"""PostgreSQL connection pool. The only module that knows how connections are configured."""

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

type Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]


def create_pool(database_url: str, *, max_size: int = 10) -> Pool:
    """Build a closed connection pool; the caller opens and closes it.

    Every connection runs in UTC, so ``timestamptz`` values always come back as UTC-aware
    datetimes regardless of how the server is configured.
    """
    return AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"options": "-c timezone=UTC"},
    )
