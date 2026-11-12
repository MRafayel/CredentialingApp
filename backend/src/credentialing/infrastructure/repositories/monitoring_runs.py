"""Recheck runs of the monitoring job in Postgres: one ``monitoring_runs`` row per run.

A shared system table, not tenant data: a run keeps counts and timings, never a provider or
an organization id.
"""

from collections.abc import Sequence
from typing import Final

from psycopg import sql
from psycopg.rows import class_row
from psycopg.types.json import Json

from credentialing.domain.models import MonitoringRun
from credentialing.infrastructure.database import ConnectionSource

_SELECT: Final = sql.SQL(
    "select id, started_at, finished_at, trigger, source_id, organizations, providers, results,"
    " changed, failures, purged, phases, cpu_time, note from monitoring_runs"
)


class PostgresMonitoringRunRepository:
    """Implements :class:`~credentialing.domain.ports.MonitoringRunRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def add_run(self, run: MonitoringRun) -> None:
        """Store a finished run; ``phases`` as ``json`` so they keep their order."""
        phases = run.model_dump(mode="json")["phases"]
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                insert into monitoring_runs (
                    id, started_at, finished_at, trigger, source_id, organizations, providers,
                    results, changed, failures, purged, phases, cpu_time, note
                ) values (
                    {run.id}, {run.started_at}, {run.finished_at}, {run.trigger},
                    {run.source_id}, {run.organizations}, {run.providers}, {run.results},
                    {run.changed}, {run.failures}, {run.purged}, {Json(phases)},
                    {run.cpu_time}, {run.note}
                )
                """
            )

    async def last_run(self) -> MonitoringRun | None:
        """The most recently finished run, if any."""
        runs = await self.runs(1)
        return runs[0] if runs else None

    async def runs(self, limit: int) -> Sequence[MonitoringRun]:
        """At most ``limit`` runs, most recently finished first."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(MonitoringRun)) as cursor,
        ):
            await cursor.execute(t"{_SELECT:q} order by finished_at desc, id limit {limit}")
            return await cursor.fetchall()
