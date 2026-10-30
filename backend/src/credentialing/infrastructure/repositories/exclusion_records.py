"""Exclusion list rows in Postgres, versioned by ``valid_from``/``valid_to``.

``latest`` marks the newest version of each row identity, whether it is still listed
(``valid_to`` is null) or was removed from the published list. Reads match against latest
rows, so a party who left a list keeps matching, with its removal visible. Shared reference
data: no organization owns them.
"""

import hashlib
import re
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
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.policies import (
    RewriteKey,
    candidate_keys,
    license_key,
    pair_rewritten_rows,
    party_key,
)
from credentialing.domain.types import Npi, SourceId, StateCode
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import mapping_jsonb, model_jsonb

_SELECT: Final = sql.SQL(
    "select id, source_id, snapshot_id, record_hash, content_hash, entity_kind, last_name,"
    " first_name, middle_name, suffix, organization_name, aliases, related_parties, npi,"
    " related_npis, state_provider_id,"
    " license_number, license_state, license_kind, date_of_birth, address, action, action_raw,"
    " effective_date, end_date, reason, raw, valid_from, valid_to, rewrites from exclusion_records"
)

MAX_SEARCH_RESULTS: Final = 50
"""The most rows one lookup returns."""

_SEARCH_TOKEN: Final = re.compile(r"[^\W_]+")
"""Runs of letters and digits: the only characters a lookup passes into a tsquery."""

_NPI_QUERY: Final = re.compile(r"[0-9]{10}")


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
    related_parties: tuple[str, ...]
    npi: str | None
    related_npis: tuple[str, ...]
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
    rewrites: UUID | None

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
            related_parties=self.related_parties,
            npi=self.npi,
            related_npis=self.related_npis,
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
            rewrites=self.rewrites,
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
        *,
        source_changed: bool,
    ) -> SnapshotDiff:
        """Make ``records`` the source's current list in one transaction and report the diff.

        Every record must belong to ``source_id`` and ``snapshot_id``. Rows that repeat an
        identity within ``records`` are stored once (the first wins). A row missing from
        ``records`` is closed (``valid_to``): when ``source_changed`` it was removed from the
        published list and stays latest, so it still matches; when the source's bytes are the
        same as last time, the parser changed and it was superseded by the re-parse
        (``reparsed``), so it loses ``latest`` too. When ``source_changed``, a missing row that
        pairs one to one with a new row by NPI and effective date
        (:func:`~credentialing.domain.policies.pair_rewritten_rows`) was rewritten by the list
        (``rewritten``): it is closed and loses ``latest``, and the new row's ``rewrites``
        points to it. A changed row's old version is
        closed and loses ``latest`` to the new one; a row identity that returns after a removal
        gets a fresh version (``readded``) and its removed one loses ``latest``. Old versions
        are updated before new ones are inserted, so the one-current-row and one-latest-row
        indexes always hold.
        Replacements of one source are serialized by a transaction-scoped advisory lock, so two
        concurrent runs cannot interleave.

        ``name_keys`` is derived from the names by the domain's normalization, not source
        content: an unchanged row whose keys no longer match that normalization gets them
        recomputed in place, so a change to the normalization reaches stored rows on the next
        sync (``--force`` when the source itself has not changed).
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
                select record_hash, content_hash, name_keys, id, npi, effective_date
                from exclusion_records
                where source_id = {source_id} and valid_to is null
                """
            )
            rows = await cursor.fetchall()
            current: dict[str, str] = {row[0]: row[1] for row in rows}
            stored_keys: dict[str, frozenset[str]] = {row[0]: frozenset(row[2]) for row in rows}
            current_ids: dict[str, UUID] = {row[0]: row[3] for row in rows}
            rewrite_keys: dict[str, RewriteKey] = {row[0]: (row[4], row[5]) for row in rows}

            vanished = sorted(current.keys() - incoming.keys())
            new = sorted(incoming.keys() - current.keys())
            cursor = await connection.execute(
                t"""
                select record_hash from exclusion_records
                where source_id = {source_id} and latest and valid_to is not null
                  and record_hash = any({new}::text[])
                """
            )
            returning = {row[0] for row in await cursor.fetchall()}
            fresh = [incoming[hash_] for hash_ in new if hash_ not in returning]
            readded = [incoming[hash_] for hash_ in new if hash_ in returning]
            pairs = (
                pair_rewritten_rows(
                    {hash_: rewrite_keys[hash_] for hash_ in vanished},
                    {r.record_hash: (r.npi, r.effective_date) for r in fresh},
                )
                if source_changed
                else {}
            )
            added = [record for record in fresh if record.record_hash not in pairs]
            rewritten = [
                record.model_copy(update={"rewrites": current_ids[pairs[record.record_hash]]})
                for record in fresh
                if record.record_hash in pairs
            ]
            removed = sorted(set(vanished) - set(pairs.values()))
            changed = [
                record
                for hash_, record in incoming.items()
                if hash_ in current and current[hash_] != record.content_hash
            ]
            if removed:
                await connection.execute(
                    t"""
                    update exclusion_records
                    set valid_to = {now}, latest = {source_changed}
                    where source_id = {source_id} and valid_to is null
                      and record_hash = any({removed}::text[])
                    """
                )
            superseded = sorted({record.record_hash for record in changed} | set(pairs.values()))
            if superseded:
                await connection.execute(
                    t"""
                    update exclusion_records set valid_to = {now}, latest = false
                    where source_id = {source_id} and valid_to is null
                      and record_hash = any({superseded}::text[])
                    """
                )
            if readded:
                returned = sorted(returning)
                await connection.execute(
                    t"""
                    update exclusion_records set latest = false
                    where source_id = {source_id} and latest and valid_to is not null
                      and record_hash = any({returned}::text[])
                    """
                )
            await _insert(connection, [*added, *rewritten, *readded, *changed], now)
            stale = [
                record
                for hash_, record in incoming.items()
                if hash_ in current
                and current[hash_] == record.content_hash
                and stored_keys[hash_] != candidate_keys(record)
            ]
            await _refresh_name_keys(connection, source_id, stale)

        return SnapshotDiff(
            added=len(added),
            removed=len(removed) if source_changed else 0,
            changed=len(changed),
            unchanged=len(incoming) - len(new) - len(changed),
            readded=len(readded),
            reparsed=0 if source_changed else len(removed),
            rewritten=len(rewritten),
        )

    async def find_candidates(
        self,
        npi: Npi | None,
        last_name: str | None,
        organization_name: str | None,
        *,
        license_numbers: Sequence[str] = (),
    ) -> Sequence[ExclusionRecord]:
        """Latest rows with this NPI (own or related), normalized name or licence number.

        Latest rows include those removed from the published list (``valid_to`` set), so a
        party who left a list still matches and its removal can be reported.

        Names are compared after the domain's normalization (organization names without their
        legal designators), against the record's names, alias and related-party words, so
        every record the matcher could match by name is returned. Licence numbers are compared
        without whitespace and case, in any state; the matcher checks the state.
        """
        keys = {normalize_name(last_name)} if last_name else set()
        if organization_name:
            keys.add(normalize_organization_name(organization_name))
        names = sorted(keys - {""})
        npis = [npi] if npi else []
        licenses = sorted({license_key(number) for number in license_numbers} - {""})
        if npi is None and not names and not licenses:
            return []
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where latest and (
                    npi = {npi}
                    or related_npis && {npis}::text[]
                    or name_keys && {names}::text[]
                    or upper(regexp_replace(license_number, '\\s+', '', 'g'))
                        = any({licenses}::text[])
                )
                order by source_id, id
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def search(
        self,
        query: str,
        *,
        limit: int,
        source_ids: Sequence[SourceId] = (),
        state: StateCode | None = None,
    ) -> Sequence[ExclusionRecord]:
        """Latest rows (current or removed from the list) matching a free-text lookup, best
        first, at most 50.

        Every run of letters and digits in ``query`` must start a word of the row's names,
        aliases, related parties, NPI, related NPIs, state provider id or licence number
        (``"stop pharm"`` finds "1 Stop Pharmacy"). A query of exactly ten digits also matches
        the NPI itself; those rows come first, then rows where more query words are whole
        words (names outrank related parties, which outrank identifiers), then by prefix
        rank, then by name. With ``state``, only rows from one of ``source_ids`` or with a
        licence or address in that state; the text match bounds the rows first, so the filter
        needs no index of its own.
        """
        term = query.strip()
        tokens = _SEARCH_TOKEN.findall(term.casefold())
        if not tokens:
            return []
        tsquery = " & ".join(f"{token}:*" for token in tokens)
        whole_words = " | ".join(tokens)
        npi = term if _NPI_QUERY.fullmatch(term) else None
        size = min(max(limit, 1), MAX_SEARCH_RESULTS)
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where latest
                  and (npi = {npi} or search_vector @@ to_tsquery('simple', {tsquery}))
                  and (
                    {state}::text is null
                    or source_id = any({list(source_ids)}::text[])
                    or license_state = {state}
                    or address->>'state' = {state}
                  )
                order by
                    coalesce(npi = {npi}, false) desc,
                    ts_rank(search_vector, to_tsquery('simple', {whole_words})) desc,
                    ts_rank(search_vector, to_tsquery('simple', {tsquery})) desc,
                    coalesce(organization_name, last_name), first_name, id
                limit {size}
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def get(self, record_id: UUID) -> ExclusionRecord | None:
        """Return the row with this id, current or not."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(t"{_SELECT:q} where id = {record_id}")
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def versions(self, source_id: SourceId, record_hash: str) -> Sequence[ExclusionRecord]:
        """Every version of one row identity in one source, current or not, oldest first."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where source_id = {source_id} and record_hash = {record_hash}
                order by valid_from, id
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def rewritten_by(self, record_id: UUID) -> ExclusionRecord | None:
        """The row that replaced this one when the list rewrote its identity, if any."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(t"{_SELECT:q} where rewrites = {record_id} order by id limit 1")
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def same_name(self, record: ExclusionRecord, *, limit: int) -> Sequence[ExclusionRecord]:
        """At most ``limit`` latest rows other than ``record`` sharing its party key.

        One bitmap scan of the ``name_keys`` GIN index; rows whose first name equals the
        record's (ignoring case) come first, so the limit is spent on the likeliest namesakes,
        then by source and id.
        """
        key = party_key(record)
        if key is None:
            return []
        first = record.person_name.first.casefold() if record.person_name else None
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where latest and id <> {record.id}
                  and name_keys @> array[{key}]::text[]
                order by coalesce(lower(first_name) = {first}, false) desc, source_id, id
                limit {max(limit, 0)}
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def same_license(
        self, record: ExclusionRecord, *, limit: int
    ) -> Sequence[ExclusionRecord]:
        """At most ``limit`` latest rows other than ``record`` with its licence and state.

        Uses the normalized licence-number index, compared the way matching compares them.
        """
        if record.license is None:
            return []
        number, state = license_key(record.license.number), record.license.state
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where latest and id <> {record.id}
                  and upper(regexp_replace(license_number, '\\s+', '', 'g')) = {number}
                  and license_state = {state}
                order by source_id, id
                limit {max(limit, 0)}
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def linked_by_npi(
        self, npis: Sequence[Npi], *, exclude_id: UUID
    ) -> Sequence[ExclusionRecord]:
        """Latest rows other than ``exclude_id`` whose own or related NPIs include ``npis``.

        One query over the NPI index and the related-NPI index, ordered by source and id.
        """
        wanted = sorted(set(npis))
        if not wanted:
            return []
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ExclusionRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where latest and id <> {exclude_id}
                  and (npi = any({wanted}::bpchar[]) or related_npis && {wanted}::text[])
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

    async def count_current(self, source_id: SourceId) -> int:
        """Return how many of the source's rows are current (``valid_to`` is null)."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"select count(*) from exclusion_records"
                t" where source_id = {source_id} and valid_to is null"
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


def _lock_key(source_id: str) -> int:
    """A stable signed 64-bit advisory lock key for one source."""
    digest = hashlib.sha256(f"exclusion_records:{source_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def _refresh_name_keys(
    connection: AsyncConnection[TupleRow], source_id: str, records: Sequence[ExclusionRecord]
) -> None:
    """Rewrite the derived ``name_keys`` of current rows, pipelined like inserts."""
    if not records:
        return
    async with connection.pipeline(), connection.cursor() as cursor:
        for record in records:
            await cursor.execute(
                t"""
                update exclusion_records set name_keys = {sorted(candidate_keys(record))}::text[]
                where source_id = {source_id} and record_hash = {record.record_hash}
                  and valid_to is null
                """
            )


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
                    first_name, middle_name, suffix, organization_name, aliases,
                    related_parties, name_keys, npi, related_npis,
                    state_provider_id, license_number, license_state, license_kind,
                    date_of_birth, address, action, action_raw, effective_date, end_date,
                    reason, raw, valid_from, valid_to, rewrites
                ) values (
                    {record.id}, {record.source_id}, {record.snapshot_id}, {record.record_hash},
                    {record.content_hash},
                    {record.entity_kind.value}, {name.last if name else None},
                    {name.first if name else None}, {name.middle if name else None},
                    {name.suffix if name else None}, {record.organization_name},
                    {list(record.aliases)}::text[], {list(record.related_parties)}::text[],
                    {sorted(candidate_keys(record))}::text[],
                    {record.npi}, {list(record.related_npis)}::text[], {record.state_provider_id},
                    {license.number if license else None}, {license.state if license else None},
                    {license.kind if license else None}, {record.date_of_birth},
                    {model_jsonb(record.address) if record.address else None},
                    {record.action.value}, {record.action_raw}, {record.effective_date},
                    {record.end_date}, {record.reason}, {mapping_jsonb(record.raw)}, {now}, null,
                    {record.rewrites}
                )
                """
            )
