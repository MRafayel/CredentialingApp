"""The database conventions every repository relies on, proven against the real local database."""

from datetime import datetime, timedelta

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow, class_row
from pydantic import BaseModel

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


class Probe(BaseModel, frozen=True):
    """Stands in for a domain model."""

    label: str
    checked_at: datetime


async def test_rows_become_validated_immutable_models(
    db_connection: AsyncConnection[TupleRow],
) -> None:
    label = "licence check"

    async with db_connection.cursor(row_factory=class_row(Probe)) as cursor:
        await cursor.execute(t"select {label}::text as label, now() as checked_at")
        probe = await cursor.fetchone()

    assert probe is not None
    assert probe.label == label


async def test_timestamps_come_back_as_utc(db_connection: AsyncConnection[TupleRow]) -> None:
    async with db_connection.cursor(row_factory=class_row(Probe)) as cursor:
        await cursor.execute("select 'x' as label, now() as checked_at")
        probe = await cursor.fetchone()

    assert probe is not None
    assert probe.checked_at.utcoffset() == timedelta(0)


async def test_values_travel_as_parameters_not_as_sql(
    db_connection: AsyncConnection[TupleRow],
) -> None:
    hostile = "x'; drop table providers; --"

    async with db_connection.cursor() as cursor:
        await cursor.execute(t"select {hostile}::text")
        row = await cursor.fetchone()

    assert row == (hostile,)


async def test_writes_are_rolled_back_between_tests(
    db_connection: AsyncConnection[TupleRow],
) -> None:
    async with db_connection.cursor() as cursor:
        await cursor.execute("select to_regclass('public.rollback_probe') is null")
        assert await cursor.fetchone() == (True,)
        await cursor.execute("create table rollback_probe (id int)")
