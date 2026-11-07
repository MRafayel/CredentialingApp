"""The NPI registry mirror in Postgres, loaded from dataset files by difference.

``npi_registry`` holds one row per NPI, its newest version; ``npi_registry_history`` the
versions a later file replaced (insert-only). Rows are never deleted. Shared reference data:
no organization owns them.

A load (:meth:`PostgresNpiRegistryRepository.load`) runs on one connection inside one
transaction, so a failure at any point leaves the mirror as it was, and is serialized by an
advisory lock. Its phases:

1. **scan**: the file's NPIs and line hashes are streamed into a temporary table with
   ``COPY`` (batches of :data:`SCAN_BATCH` pre-formatted lines built in a worker thread while
   the previous batch is sent). A repeated NPI keeps its last occurrence.
2. **compare**: one join finds the rows that are new, changed (another hash) or back in the
   file after a full file dropped them; nothing else is read again.
3. **read**: only those rows are parsed (``Dataset.records``, in the thread) and copied into a
   temporary table in batches of :data:`READ_BATCH`; on the first load, when every row is new,
   straight into the mirror.
4. **apply**: set operations: replaced rows are copied to the history, then upserted;
   rows older than the mirror's (``Last Update Date``) are left alone; a full file flags the
   rows it no longer lists (``in_file`` false, versioned too).

A deactivation file reads every row (a few hundred thousand stubs) before comparing, since
what it changes is a status and a date, not a line. ``work_mem`` is raised for the
transaction so the joins over ten million rows stay in memory; the mirror is analyzed after
a load that wrote rows.
"""

import asyncio
import hashlib
import itertools
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, LiteralString
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.rows import TupleRow, class_row
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import DatasetLoadMode, EntityKind, NpiStatus
from credentialing.domain.errors import DatasetFormatError, DatasetShrankError
from credentialing.domain.models import (
    Address,
    AuthorizedOfficial,
    DatasetLoad,
    NpiRecord,
    NpiTaxonomy,
    PersonName,
)
from credentialing.domain.ports import Dataset
from credentialing.domain.types import Npi
from credentialing.infrastructure.database import ConnectionSource

logger = logging.getLogger(__name__)

SCAN_BATCH: Final = 50_000
"""NPI and hash pairs sent per ``COPY`` write in the scan phase."""

READ_BATCH: Final = 10_000
"""Records formatted per ``COPY`` write in the read phase."""

WORK_MEM: Final = "256MB"
"""``work_mem`` for the load's transaction: the joins and the duplicate count over ten
million rows then run in memory instead of spilling to disk."""

_COLUMNS: Final = (
    "npi",
    "entity_kind",
    "status",
    "replacement_npi",
    "last_name",
    "first_name",
    "middle_name",
    "name_prefix",
    "suffix",
    "credential",
    "organization_name",
    "other_names",
    "sole_proprietor",
    "organization_subpart",
    "enumeration_date",
    "last_updated",
    "certification_date",
    "deactivation_date",
    "deactivation_reason",
    "reactivation_date",
    "mailing_address",
    "practice_address",
    "taxonomies",
    "authorized_official",
    "line_hash",
    "in_file",
    "snapshot_id",
    "first_seen_at",
    "updated_at",
)
"""The mirror's columns, in the order rows are copied."""

_COLUMN_LIST: Final = sql.SQL(", ").join(sql.Identifier(name) for name in _COLUMNS)
_REGISTRY_COLUMNS: Final = sql.SQL(", ").join(sql.Identifier("r", name) for name in _COLUMNS)
_INCOMING_COLUMNS: Final = sql.SQL(", ").join(sql.Identifier("i", name) for name in _COLUMNS)
_UPSERT_SET: Final = sql.SQL(", ").join(
    sql.SQL("{column} = excluded.{column}").format(column=sql.Identifier(name))
    for name in _COLUMNS
    if name not in ("npi", "first_seen_at")
)
_SELECT: Final = sql.SQL("select {columns} from npi_registry").format(columns=_COLUMN_LIST)

_COPY_SPECIAL: Final = re.compile(r"[\\\t\n\r]")
_COPY_ESCAPES: Final = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"})
_NULL: Final = "\\N"


def _lock_key() -> int:
    """A stable signed 64-bit advisory lock key for the mirror."""
    digest = hashlib.sha256(b"npi_registry").digest()
    return int.from_bytes(digest[:8], "big", signed=True)


_LOCK_KEY: Final = _lock_key()


def copy_text_row(values: Sequence[str | None]) -> str:
    """One line of ``COPY ... FROM STDIN`` text format: tab-separated, ``\\N`` for null.

    Backslash, tab, newline and carriage return inside a value are escaped as COPY reads
    them; the common case (no such character) is a single join.
    """
    if _COPY_SPECIAL.search("".join(value for value in values if value is not None)):
        return (
            "\t".join(
                _NULL if value is None else value.translate(_COPY_ESCAPES) for value in values
            )
            + "\n"
        )
    return "\t".join(_NULL if value is None else value for value in values) + "\n"


def array_literal(items: Sequence[str]) -> str:
    """A Postgres ``text[]`` literal: every element quoted, quotes and backslashes escaped."""
    quoted = ('"' + item.replace("\\", "\\\\").replace('"', '\\"') + '"' for item in items)
    return "{" + ",".join(quoted) + "}"


class PostgresNpiRegistryRepository:
    """Implements :class:`~credentialing.domain.ports.NpiRegistryRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def load(
        self,
        dataset: Dataset,
        *,
        mode: DatasetLoadMode,
        snapshot_id: UUID,
        now: datetime,
        min_rows: int | None = None,
    ) -> DatasetLoad:
        """Apply ``dataset`` to the mirror in one transaction (module docstring) and report it.

        Raises:
            DatasetShrankError: ``min_rows`` is given and the file has fewer distinct NPIs.
            DatasetFormatError: A row is malformed, or the second pass did not return the rows
                the first one listed.
        """
        phases: dict[str, timedelta] = {}
        async with self._database.connection() as connection:
            async with connection.transaction():
                await connection.execute(t"select pg_advisory_xact_lock({_LOCK_KEY})")
                await connection.execute(t"select set_config('work_mem', {WORK_MEM}, true)")
                await _create_work_tables(connection)
                loader = _Load(connection, dataset, mode, snapshot_id, now, phases)
                result = await loader.run(min_rows)
            if result.added + result.changed + result.missing + result.deactivated:
                async with connection.transaction():
                    await connection.execute("analyze npi_registry")
        for phase, spent in phases.items():
            logger.info(
                "npi registry load snapshot_id=%s mode=%s phase=%s seconds=%.1f",
                snapshot_id,
                mode.value,
                phase,
                spent.total_seconds(),
            )
        return result

    async def get(self, npi: Npi) -> NpiRecord | None:
        """Return the mirrored record of this NPI, listed in the latest full file or not."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_NpiRow)) as cursor,
        ):
            await cursor.execute(t"{_SELECT:q} where npi = {npi}")
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def count(self, *, status: NpiStatus | None = None) -> int:
        """Return how many mirrored NPIs the latest file lists, optionally with ``status``."""
        wanted = status.value if status else None
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select count(*) from npi_registry
                where in_file and ({wanted}::text is null or status = {wanted})
                """
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


@dataclass(frozen=True, slots=True)
class _Counts:
    """What the compare and apply phases found (see :class:`DatasetLoad`)."""

    added: int = 0
    changed: int = 0
    outdated: int = 0
    unchanged: int = 0
    missing: int = 0
    deactivated: int = 0


class _Load:
    """One load's steps on one connection inside its transaction."""

    def __init__(
        self,
        connection: AsyncConnection[TupleRow],
        dataset: Dataset,
        mode: DatasetLoadMode,
        snapshot_id: UUID,
        now: datetime,
        phases: dict[str, timedelta],
    ) -> None:
        self._connection = connection
        self._dataset = dataset
        self._mode = mode
        self._snapshot_id = snapshot_id
        self._now = now
        self._phases = phases

    async def run(self, min_rows: int | None) -> DatasetLoad:
        with self._phase("scan"):
            scanned = await self._scan()
            duplicates = await self._drop_repeats()
        distinct = scanned - duplicates
        if min_rows is not None and distinct < min_rows:
            raise DatasetShrankError(distinct, min_rows)
        if self._mode is DatasetLoadMode.DEACTIVATIONS:
            counts = await self._deactivations(distinct)
        else:
            counts = await self._rows(distinct, repeats=duplicates > 0)
        return DatasetLoad(
            mode=self._mode,
            scanned=scanned,
            added=counts.added,
            changed=counts.changed,
            unchanged=counts.unchanged,
            outdated=counts.outdated,
            missing=counts.missing,
            deactivated=counts.deactivated,
            duplicates=duplicates,
            phases=dict(self._phases),
        )

    @contextmanager
    def _phase(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            spent = timedelta(seconds=time.perf_counter() - started)
            self._phases[name] = self._phases.get(name, timedelta(0)) + spent

    async def _scan(self) -> int:
        """Copy every NPI and line hash of the file into ``npi_scan``; return the row count."""
        rows = self._dataset.scan()
        count = 0

        def batch() -> bytes | None:
            nonlocal count
            lines = []
            for npi, line_hash in itertools.islice(rows, SCAN_BATCH):
                count += 1
                lines.append(f"{npi}\t{line_hash}\t{count}\n")
            return "".join(lines).encode() if lines else None

        await _copy(self._connection, "copy npi_scan (npi, line_hash, seq) from stdin", batch)
        await self._connection.execute("analyze npi_scan")
        return count

    async def _drop_repeats(self) -> int:
        """Keep the last occurrence of an NPI the file repeats; return how many were dropped."""
        cursor = await self._connection.execute(
            "select count(*) - count(distinct npi) from npi_scan"
        )
        row = await cursor.fetchone()
        duplicates = int(row[0]) if row else 0
        if duplicates:
            logger.warning(
                "npi registry load snapshot_id=%s repeated_npis=%d", self._snapshot_id, duplicates
            )
            await self._connection.execute(
                """
                delete from npi_scan a using npi_scan b
                where a.npi = b.npi and a.seq < b.seq
                """
            )
        return duplicates

    async def _rows(self, distinct: int, *, repeats: bool) -> _Counts:
        """A full or incremental file: compare hashes, read the differing rows, apply them.

        When every row is new and none repeats (the first load), the rows are copied straight
        into the mirror instead of through ``npi_incoming``: ten million rows written once.
        """
        connection = self._connection
        with self._phase("compare"):
            await connection.execute(
                """
                create temporary table npi_needed on commit drop as
                select s.npi, r.npi is null as is_new
                from npi_scan s left join npi_registry r on r.npi = s.npi
                where r.npi is null or r.line_hash <> s.line_hash or not r.in_file
                """
            )
            needed, new = await _counts(
                connection, "select count(*), count(*) filter (where is_new) from npi_needed"
            )
            wanted = None if needed == distinct else await _needed_npis(connection)
        direct = needed == new == distinct and needed > 0 and not repeats
        outdated = 0
        if needed:
            with self._phase("read"):
                target = "npi_registry" if direct else "npi_incoming"
                copied = await self._read(target, wanted)
            if not direct:
                copied = await self._dedupe_incoming()
            if copied != needed:
                raise DatasetFormatError(
                    f"the second pass read {copied} of the {needed} rows the first pass listed"
                )
        with self._phase("apply"):
            if needed and not direct:
                outdated = await self._apply_rows()
            missing = (
                await self._flag_missing(distinct) if self._mode is DatasetLoadMode.FULL else 0
            )
        return _Counts(
            added=new,
            changed=needed - new - outdated,
            outdated=outdated,
            unchanged=distinct - needed,
            missing=missing,
        )

    async def _deactivations(self, distinct: int) -> _Counts:
        """A deactivation file: read every stub, then set status and date where they differ."""
        connection = self._connection
        with self._phase("read"):
            copied = await self._read("npi_incoming", None)
            copied = await self._dedupe_incoming() if copied != distinct else copied
        if copied != distinct:
            raise DatasetFormatError(
                f"the second pass read {copied} of the {distinct} rows the first pass listed"
            )
        with self._phase("compare"):
            await connection.execute(
                """
                create temporary table npi_needed on commit drop as
                select i.npi, r.npi is null as is_new,
                    coalesce(r.reactivation_date >= i.deactivation_date, false) as outdated
                from npi_incoming i left join npi_registry r on r.npi = i.npi
                where r.npi is null or r.status <> 'deactivated'
                    or r.deactivation_date is distinct from i.deactivation_date
                """
            )
            needed, new, outdated = await _counts(
                connection,
                """
                select count(*), count(*) filter (where is_new),
                    count(*) filter (where outdated)
                from npi_needed
                """,
            )
        with self._phase("apply"):
            await connection.execute(
                t"""
                insert into npi_registry_history ({_COLUMN_LIST:q}, valid_from, valid_to)
                select {_REGISTRY_COLUMNS:q}, r.updated_at, {self._now}
                from npi_registry r join npi_needed n on n.npi = r.npi
                where not n.is_new and not n.outdated
                """
            )
            await connection.execute(
                t"""
                update npi_registry r
                set status = {NpiStatus.DEACTIVATED.value},
                    deactivation_date = i.deactivation_date,
                    updated_at = {self._now}, snapshot_id = {self._snapshot_id}
                from npi_incoming i join npi_needed n on n.npi = i.npi
                where r.npi = i.npi and not n.is_new and not n.outdated
                """
            )
            await connection.execute(
                t"""
                insert into npi_registry ({_COLUMN_LIST:q})
                select {_INCOMING_COLUMNS:q}
                from npi_incoming i join npi_needed n on n.npi = i.npi
                where n.is_new
                """
            )
        changed = needed - new - outdated
        return _Counts(
            added=new,
            changed=changed,
            outdated=outdated,
            unchanged=distinct - needed,
            deactivated=new + changed,
        )

    async def _read(self, target: str, wanted: set[str] | None) -> int:
        """Copy the records of ``wanted`` (``None``: all) into ``target``; return how many."""
        records = self._dataset.records(wanted)
        snapshot_id, now = str(self._snapshot_id), self._now.isoformat()
        count = 0

        def batch() -> bytes | None:
            nonlocal count
            lines = [
                copy_text_row(_values(record, snapshot_id, now))
                for record in itertools.islice(records, READ_BATCH)
            ]
            count += len(lines)
            return "".join(lines).encode() if lines else None

        statement = sql.SQL("copy {table} ({columns}) from stdin").format(
            table=sql.Identifier(target), columns=_COLUMN_LIST
        )
        await _copy(self._connection, statement, batch)
        return count

    async def _dedupe_incoming(self) -> int:
        """Drop incoming rows that are not the scan's chosen occurrence of a repeated NPI (and
        exact repeats); return how many rows remain."""
        connection = self._connection
        await connection.execute(
            """
            delete from npi_incoming i
            where not exists (
                select 1 from npi_scan s where s.npi = i.npi and s.line_hash = i.line_hash
            )
            """
        )
        await connection.execute(
            """
            delete from npi_incoming a using npi_incoming b
            where a.npi = b.npi and a.ctid < b.ctid
            """
        )
        (remaining,) = await _counts(connection, "select count(*) from npi_incoming")
        return remaining

    async def _apply_rows(self) -> int:
        """Version and upsert the incoming rows; return how many were older than the mirror."""
        connection = self._connection
        cursor = await connection.execute(
            """
            delete from npi_incoming i using npi_registry r
            where r.npi = i.npi and r.last_updated > i.last_updated
            """
        )
        outdated = max(cursor.rowcount, 0)
        await connection.execute(
            t"""
            insert into npi_registry_history ({_COLUMN_LIST:q}, valid_from, valid_to)
            select {_REGISTRY_COLUMNS:q}, r.updated_at, {self._now}
            from npi_registry r join npi_incoming i on i.npi = r.npi
            """
        )
        await connection.execute(
            t"""
            insert into npi_registry ({_COLUMN_LIST:q})
            select {_COLUMN_LIST:q} from npi_incoming
            on conflict (npi) do update set {_UPSERT_SET:q}
            """
        )
        return outdated

    async def _flag_missing(self, distinct: int) -> int:
        """Version and flag the mirrored rows a full file no longer lists; return how many.

        After the upsert every one of the file's ``distinct`` NPIs is listed, so when the
        mirror lists exactly that many rows none is missing and the anti-join is skipped (one
        count instead of a join over ten million rows each; the first load spent three
        minutes in it). Otherwise the anti-join runs once, into ``npi_missing``; versioning
        and flagging then join that short list.
        """
        connection = self._connection
        (listed,) = await _counts(connection, "select count(*) from npi_registry where in_file")
        if listed == distinct:
            return 0
        await connection.execute(
            """
            create temporary table npi_missing on commit drop as
            select r.npi from npi_registry r
            where r.in_file and not exists (select 1 from npi_scan s where s.npi = r.npi)
            """
        )
        (missing,) = await _counts(connection, "select count(*) from npi_missing")
        if not missing:
            return 0
        await connection.execute(
            t"""
            insert into npi_registry_history ({_COLUMN_LIST:q}, valid_from, valid_to)
            select {_REGISTRY_COLUMNS:q}, r.updated_at, {self._now}
            from npi_registry r join npi_missing m on m.npi = r.npi
            """
        )
        await connection.execute(
            t"""
            update npi_registry r
            set in_file = false, updated_at = {self._now}, snapshot_id = {self._snapshot_id}
            from npi_missing m where m.npi = r.npi
            """
        )
        return missing


async def _create_work_tables(connection: AsyncConnection[TupleRow]) -> None:
    """The load's temporary tables, dropped first in case an earlier load on this connection
    ran inside an outer transaction (tests), where ``on commit drop`` has not fired yet."""
    await connection.execute(
        "drop table if exists pg_temp.npi_scan, pg_temp.npi_needed, pg_temp.npi_incoming,"
        " pg_temp.npi_missing"
    )
    await connection.execute(
        """
        create temporary table npi_scan (
            npi char(10) not null, line_hash text not null, seq bigint not null
        ) on commit drop
        """
    )
    await connection.execute(
        """
        create temporary table npi_incoming (like npi_registry including defaults)
        on commit drop
        """
    )


async def _copy(
    connection: AsyncConnection[TupleRow],
    statement: LiteralString | sql.Composed,
    batch: Callable[[], bytes | None],
) -> None:
    """Run ``COPY ... FROM STDIN`` with the batches ``batch`` builds in a worker thread.

    The next batch is built while the current one is sent, so parsing and the database work
    side by side; ``batch`` returns ``None`` when the data is exhausted.
    """
    async with connection.cursor() as cursor, cursor.copy(statement) as copy:
        pending: Awaitable[bytes | None] = asyncio.ensure_future(asyncio.to_thread(batch))
        try:
            while (chunk := await pending) is not None:
                pending = asyncio.ensure_future(asyncio.to_thread(batch))
                await copy.write(chunk)
        finally:
            await asyncio.gather(pending, return_exceptions=True)


async def _counts(connection: AsyncConnection[TupleRow], query: LiteralString) -> tuple[int, ...]:
    cursor = await connection.execute(query)
    row = await cursor.fetchone()
    return tuple(int(value) for value in row) if row else ()


async def _needed_npis(connection: AsyncConnection[TupleRow]) -> set[str]:
    """The NPIs the compare phase selected, streamed out with ``COPY ... TO STDOUT``."""
    wanted: set[str] = set()
    buffer = b""
    async with (
        connection.cursor() as cursor,
        cursor.copy("copy (select npi from npi_needed) to stdout") as copy,
    ):
        async for data in copy:
            buffer += bytes(data)
            *lines, buffer = buffer.split(b"\n")
            wanted.update(line.decode("ascii") for line in lines)
    if buffer:
        wanted.add(buffer.decode("ascii"))
    return wanted


def _values(record: NpiRecord, snapshot_id: str, now: str) -> list[str | None]:
    """A record as the mirror's columns (:data:`_COLUMNS`), as ``COPY`` text values."""
    name = record.person_name
    return [
        record.npi,
        record.entity_kind.value,
        record.status.value,
        record.replacement_npi,
        name.last if name else None,
        name.first if name else None,
        name.middle if name else None,
        record.name_prefix,
        name.suffix if name else None,
        record.credential,
        record.organization_name,
        array_literal(record.other_names),
        _flag(record.sole_proprietor),
        _flag(record.organization_subpart),
        _date(record.enumeration_date),
        _date(record.last_updated),
        _date(record.certification_date),
        _date(record.deactivation_date),
        record.deactivation_reason,
        _date(record.reactivation_date),
        _json(record.mailing_address),
        _json(record.practice_address),
        json.dumps([taxonomy.model_dump(mode="json") for taxonomy in record.taxonomies])
        if record.taxonomies
        else "[]",
        _json(record.authorized_official),
        record.line_hash,
        "t",
        snapshot_id,
        now,
        now,
    ]


def _flag(value: bool | None) -> str | None:
    return None if value is None else ("t" if value else "f")


def _date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _json(model: BaseModel | None) -> str | None:
    return model.model_dump_json() if model is not None else None


class _NpiRow(BaseModel):
    """One ``npi_registry`` row, mapped into an :class:`NpiRecord` by ``to_domain``."""

    model_config = ConfigDict(frozen=True)

    npi: str
    entity_kind: EntityKind
    status: NpiStatus
    replacement_npi: str | None
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    name_prefix: str | None
    suffix: str | None
    credential: str | None
    organization_name: str | None
    other_names: tuple[str, ...]
    sole_proprietor: bool | None
    organization_subpart: bool | None
    enumeration_date: date | None
    last_updated: date | None
    certification_date: date | None
    deactivation_date: date | None
    deactivation_reason: str | None
    reactivation_date: date | None
    mailing_address: Address | None
    practice_address: Address | None
    taxonomies: tuple[NpiTaxonomy, ...]
    authorized_official: AuthorizedOfficial | None
    line_hash: str
    in_file: bool
    snapshot_id: UUID
    first_seen_at: datetime
    updated_at: datetime

    def to_domain(self) -> NpiRecord:
        person_name = (
            PersonName(
                last=self.last_name or "",
                first=self.first_name or "",
                middle=self.middle_name,
                suffix=self.suffix,
            )
            if self.last_name is not None or self.first_name is not None
            else None
        )
        return NpiRecord(
            npi=self.npi,
            entity_kind=self.entity_kind,
            status=self.status,
            replacement_npi=self.replacement_npi,
            person_name=person_name,
            name_prefix=self.name_prefix,
            credential=self.credential,
            organization_name=self.organization_name,
            other_names=self.other_names,
            sole_proprietor=self.sole_proprietor,
            organization_subpart=self.organization_subpart,
            enumeration_date=self.enumeration_date,
            last_updated=self.last_updated,
            certification_date=self.certification_date,
            deactivation_date=self.deactivation_date,
            deactivation_reason=self.deactivation_reason,
            reactivation_date=self.reactivation_date,
            mailing_address=self.mailing_address,
            practice_address=self.practice_address,
            taxonomies=self.taxonomies,
            authorized_official=self.authorized_official,
            line_hash=self.line_hash,
            snapshot_id=self.snapshot_id,
            first_seen_at=self.first_seen_at,
            updated_at=self.updated_at,
            in_file=self.in_file,
        )
