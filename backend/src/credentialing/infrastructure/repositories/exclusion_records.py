"""Exclusion list rows in Postgres, versioned by ``valid_from``/``valid_to``.

Shared reference data: no organization owns them.
"""

import hashlib
from collections.abc import Sequence
from datetime import date, datetime
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.rows import TupleRow, class_row
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import EntityKind, ExclusionAction
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    PersonName,
    SnapshotDiff,
)
from credentialing.domain.names import normalize_name
from credentialing.domain.policies import candidate_keys
from credentialing.domain.types import Npi, SourceId
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import mapping_jsonb, model_jsonb

_SELECT: Final = sql.SQL(
    "select id, source_id, snapshot_id, record_hash, content_hash, entity_kind, last_name,"
    " first_name, middle_name, suffix, organization_name, aliases, npi, state_provider_id,"
    " license_number, license_state, license_kind, date_of_birth, address, action, action_raw,"
    " effective_date, end_date, reason, raw, valid_from, valid_to from exclusion_records"
)


class _ExclusionRow(BaseModel):
    """One ``exclusion_records`` row, mapped into an :class:`ExclusionRecord` by ``to_domain``."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    source_id: str
    snapshot_id: UUID
    record_hash: str
    content_hash: str
    entity_kind: EntityKind
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    suffix: str | None
    organization_name: str | None
    aliases: tuple[str, ...]
    npi: str | None
    state_provider_id: str | None
    license_number: str | None
    license_state: str | None
    license_kind: str | None
    date_of_birth: date | None
    address: Address | None
    action: ExclusionAction
    action_raw: str | None
    effective_date: date | None
    end_date: date | None
    reason: str | None
    raw: dict[str, str]
    valid_from: datetime
    valid_to: datetime | None

    def to_domain(self) -> ExclusionRecord:
        person_name = (
            PersonName(
                last=self.last_name,
                first=self.first_name or "",
                middle=self.middle_name,
                suffix=self.suffix,
            )
            if self.last_name is not None
            else None
        )
        license = (
            License(number=self.license_number, state=self.license_state, kind=self.license_kind)
            if self.license_number is not None and self.license_state is not None
            else None
        )
        return ExclusionRecord(
            id=self.id,
            source_id=self.source_id,
            snapshot_id=self.snapshot_id,
            entity_kind=self.entity_kind,
            person_name=person_name,
            organization_name=self.organization_name,
            aliases=self.aliases,
            npi=self.npi,
            state_provider_id=self.state_provider_id,
            license=license,
            date_of_birth=self.date_of_birth,
            address=self.address,
            action=self.action,
            action_raw=self.action_raw,
            effective_date=self.effective_date,
            end_date=self.end_date,
            reason=self.reason,
            raw=self.raw,
            record_hash=self.record_hash,
            content_hash=self.content_hash,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )


class PostgresExclusionRecordRepository:
    """Implements :class:`~credentialing.domain.ports.ExclusionRecordRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def replace_current(
        self,
        source_id: SourceId,
        snapshot_id: UUID,
        records: Sequence[ExclusionRecord],
        now: datetime,
    ) -> SnapshotDiff:
        """Make ``records`` the source's current list in one transaction and report the diff.

        Every record must belong to ``source_id`` and ``snapshot_id``. Rows that repeat an
        identity within ``records`` are stored once (the first wins). Old versions are closed
        before new ones are inserted, so the one-current-row-per-identity index always holds.
        Replacements of one source are serialized by a transaction-scoped advisory lock, so two
        concurrent runs cannot interleave.
        """
        incoming: dict[str, ExclusionRecord] = {}
        for record in records:
            if record.source_id != source_id or record.snapshot_id != snapshot_id:
                raise ValueError(
                    f"record {record.id} does not belong to {source_id} snapshot {snapshot_id}"
                )
            incoming.setdefault(record.record_hash, record)

        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(t"select pg_advisory_xact_lock({_lock_key(source_id)})")
            cursor = await connection.execute(
                t"""
                select record_hash, content_hash from exclusion_records
                where source_id = {source_id} and valid_to is null
                """
            )
            current: dict[str, str] = {row[0]: row[1] for row in await cursor.fetchall()}

            removed = sorted(current.keys() - incoming.keys())
            added = [record for hash_, record in incoming.items() if hash_ not in current]
            changed = [
                record
                for hash_, record in incoming.items()
                if hash_ in current and current[hash_] != record.content_hash
            ]
            closing = removed + [record.record_hash for record in changed]
            if closing:
                await connection.execute(
                    t"""
                    update exclusion_records set valid_to = {now}
                    where source_id = {source_id} and valid_to is null
                      and record_hash = any({closing}::text[])
                    """
                )
            await _insert(connection, added + changed, now)

        return SnapshotDiff(
            added=len(added),
            removed=len(removed),
            changed=len(changed),
            unchanged=len(incoming) - len(added) - len(changed),
        )

    async def find_candidates(
        self, npi: Npi | None, last_name: str | None, organization_name: str | None
    ) -> Sequence[ExclusionRecord]:
        """Current rows with this NPI, or this normalized last name or organization name.

        Names are compared after the domain's normalization, against the record's names and
        alias words, so every record the matcher could match by name is returned.
        """
        keys = sorted(
            {normalize_name(name) for name in (last_name, organization_name) if name} - {""}
        )
        if npi is None and not keys:
            return []
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where valid_to is null and (npi = {npi} or name_keys && {keys}::text[])
                order by source_id, id
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def get_many(self, ids: Sequence[UUID]) -> Sequence[ExclusionRecord]:
        """Return the rows with these ids, current or not, in no particular order."""
        if not ids:
            return []
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(t"{_SELECT:q} where id = any({list(ids)}::uuid[])")
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]


def _lock_key(source_id: str) -> int:
    """A stable signed 64-bit advisory lock key for one source."""
    digest = hashlib.sha256(f"exclusion_records:{source_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def _insert(
    connection: AsyncConnection[TupleRow], records: Sequence[ExclusionRecord], now: datetime
) -> None:
    """Insert records as current as of ``now``, pipelined so large lists take few round trips."""
    if not records:
        return
    async with connection.pipeline(), connection.cursor() as cursor:
        for record in records:
            name = record.person_name
            license = record.license
            await cursor.execute(
                t"""
                insert into exclusion_records (
                    id, source_id, snapshot_id, record_hash, content_hash, entity_kind, last_name,
                    first_name, middle_name, suffix, organization_name, aliases, name_keys, npi,
                    state_provider_id, license_number, license_state, license_kind,
                    date_of_birth, address, action, action_raw, effective_date, end_date,
                    reason, raw, valid_from, valid_to
                ) values (
                    {record.id}, {record.source_id}, {record.snapshot_id}, {record.record_hash},
                    {record.content_hash},
                    {record.entity_kind.value}, {name.last if name else None},
                    {name.first if name else None}, {name.middle if name else None},
                    {name.suffix if name else None}, {record.organization_name},
                    {list(record.aliases)}::text[], {sorted(candidate_keys(record))}::text[],
                    {record.npi}, {record.state_provider_id},
                    {license.number if license else None}, {license.state if license else None},
                    {license.kind if license else None}, {record.date_of_birth},
                    {model_jsonb(record.address) if record.address else None},
                    {record.action.value}, {record.action_raw}, {record.effective_date},
                    {record.end_date}, {record.reason}, {mapping_jsonb(record.raw)}, {now}, null
                )
                """
            )
