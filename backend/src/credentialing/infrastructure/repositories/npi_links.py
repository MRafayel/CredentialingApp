"""Links from exclusion records to NPIs of the registry mirror, and linking runs, in Postgres.

``exclusion_npi_links`` holds one row per (record, candidate NPI) with its match, or one row
with no NPI and level ``none`` for a record checked and linked to nothing; a record's rows are
rewritten whole by each run. ``npi_link_runs`` keeps one row per run. Shared reference data:
no organization owns them. Inferred NPIs never reach ``exclusion_records.npi``.

Candidate discovery (:meth:`PostgresNpiLinkRepository.candidates`) is one statement per batch:
the records' name keys are computed in SQL (``name_key``, ``organization_key``, the same
functions that generate the mirror's key columns), each name shape is one lateral index lookup
on the mirror (at most :data:`SCAN_CAP` rows each), and the rows found are ranked per record by
cheap shared evidence (ZIP, licence, city, middle initial, active) so the
:data:`~credentialing.domain.policies.CANDIDATE_CAP` returned are the likeliest. A roster
provider without an NPI (:meth:`PostgresNpiLinkRepository.candidates_for`) goes through the
same statement (:func:`_discovery`), fed with its values instead of record rows. Scoring is
the domain's, in Python, on what this returns.
"""

import hashlib
import logging
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from string.templatelib import Template
from typing import Final
from uuid import UUID

from psycopg import sql
from psycopg.rows import class_row
from psycopg.types.json import Json, Jsonb
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import MatchLevel
from credentialing.domain.models import (
    LinkParty,
    MatchEvidence,
    MatchResult,
    NpiLink,
    NpiLinkRun,
    NpiLinkSummary,
    NpiRecord,
)
from credentialing.domain.policies import CANDIDATE_CAP, level_rank
from credentialing.domain.ports import LinkCandidates
from credentialing.domain.types import SourceId
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.exclusion_records import (
    PostgresExclusionRecordRepository,
)
from credentialing.infrastructure.repositories.npi_registry import MIRROR_COLUMNS, NpiRow

logger = logging.getLogger(__name__)

SCAN_CAP: Final = 10_000
"""The most mirror rows one name shape of one record reads before ranking. A very common
name ("Smith", first initial "J") has a few thousand; the cap only bounds a pathological
query, and the rows past it are never weighed."""


def _lock_key() -> int:
    """A stable signed 64-bit advisory lock key for rewriting links."""
    digest = hashlib.sha256(b"exclusion_npi_links").digest()
    return int.from_bytes(digest[:8], "big", signed=True)


_LOCK_KEY: Final = _lock_key()

_PARTY_ID: Final = UUID(int=0)
"""The id the one party of :meth:`PostgresNpiLinkRepository.candidates_for` carries through
the discovery statement (a party has no row of its own)."""

_LEVELS: Final = [level.value for level in sorted(MatchLevel, key=level_rank)]
"""Level values best first: ``array_position`` over it ranks links in SQL."""

_LINK_COLUMNS: Final = (
    "id, record_id, npi, level, evidence, explanation, candidates, mirror_snapshot_id, linked_at"
)

_CANDIDATE_COLUMNS: Final = sql.SQL(", ").join(sql.Identifier("n", name) for name in MIRROR_COLUMNS)

_RUN_SELECT: Final = sql.SQL(
    "select id, started_at, finished_at, trigger, mirror_snapshot_id, records, linked,"
    " by_level, phases, cpu_time, note from npi_link_runs"
)


def _discovery(recs: Template) -> Template:
    """The discovery statement over ``recs``, the parties to find: one row per party with
    ``id``, ``entity_kind``, ``npi``, ``aliases``, the name keys (``last_key``, ``first_key``,
    ``middle_key``, ``org_key``) and arrays of its places and licences (``zips``, ``cities``
    with ``states`` pairwise, ``licenses`` with ``license_states`` pairwise for the numbered
    licences, ``licensed_states`` for every licence state, numbered or not). Records and
    roster providers go through this one statement, so their discovery cannot drift.

    Candidates are ranked by cheap evidence, highest score first, then by NPI: the same
    5-digit ZIP 4, the same licence (number and state) 4, the same city and state 2, a
    numbered taxonomy licence in one of the party's licence states 1 (below city and state,
    so a licence state alone only breaks ties), the same middle initial 1, an active NPI 1."""
    return t"""
        with recs as ({recs:q}),
        people as (
            select id, last_key as last, first_key as first from recs
            where entity_kind = 'individual' and npi is null
            union
            select r.id, name_key(split_part(a.alias, ',', 1)),
                split_part(name_key(substr(a.alias, strpos(a.alias, ',') + 1)), ' ', 1)
            from recs r cross join unnest(r.aliases) as a (alias)
            where r.entity_kind = 'individual' and r.npi is null
              and strpos(a.alias, ',') > 0
            union
            select r.id, p.last, p.first
            from recs r cross join unnest(r.aliases) as a (alias)
                cross join lateral (
                    select string_to_array(name_key(a.alias), ' ') as words
                ) as w
                cross join lateral (
                    values (w.words[cardinality(w.words)], w.words[1]),
                        (w.words[1], w.words[2])
                ) as p (last, first)
            where r.entity_kind = 'individual' and r.npi is null
              and strpos(a.alias, ',') = 0 and cardinality(w.words) >= 2
        ),
        organizations as (
            select id, org_key as key from recs
            where entity_kind = 'organization' and npi is null
            union
            select r.id, organization_key(a.alias)
            from recs r cross join unnest(r.aliases) as a (alias)
            where r.entity_kind = 'organization' and r.npi is null
        ),
        hits as (
            select p.id, n.npi from people p cross join lateral (
                select n.npi from npi_registry n
                where n.in_file and n.last_name_key = p.last
                  and n.first_name_key = p.first
                limit {SCAN_CAP}
            ) as n
            where p.last is not null and p.first is not null
            union all
            select p.id, n.npi from people p cross join lateral (
                select n.npi from npi_registry n
                where n.in_file and n.last_name_key = p.last
                  and n.first_name_key = left(p.first, 1)
                limit {SCAN_CAP}
            ) as n
            where p.last is not null and length(p.first) > 1
            union all
            select p.id, n.npi from people p cross join lateral (
                select n.npi from npi_registry n
                where n.in_file and n.last_name_key = p.last
                  and n.first_name_key >= p.first
                  and n.first_name_key < chr(ascii(p.first) + 1)
                limit {SCAN_CAP}
            ) as n
            where p.last is not null and length(p.first) = 1
            union all
            select o.id, n.npi from organizations o cross join lateral (
                select n.npi from npi_registry n
                where n.in_file and n.organization_key = o.key
                limit {SCAN_CAP}
            ) as n
            where o.key is not null
            union all
            select o.id, n.npi from organizations o cross join lateral (
                select n.npi from npi_registry n
                where n.in_file and n.other_name_keys @> array[o.key]
                limit {SCAN_CAP}
            ) as n
            where o.key is not null
            union all
            select r.id, n.npi from recs r join npi_registry n on n.npi = r.npi
        ),
        ranked as (
            select f.id as record_id,
                least(count(*) over (partition by f.id), {CANDIDATE_CAP + 1}) as found,
                row_number() over (
                    partition by f.id
                    order by
                        coalesce(r.zips && array[
                            substring(n.practice_address->>'postal_code'
                                from '^[0-9]{{5}}'),
                            substring(n.mailing_address->>'postal_code'
                                from '^[0-9]{{5}}')
                        ], false)::int * 4
                        + exists (
                            select 1 from jsonb_array_elements(n.taxonomies) as t
                                cross join unnest(r.licenses, r.license_states)
                                    as l (license, state)
                            where t->>'license_state' = l.state
                              and license_key(t->>'license_number') = l.license
                        )::int * 4
                        + exists (
                            select 1 from jsonb_array_elements(n.taxonomies) as t
                            where t->>'license_state' = any(r.licensed_states)
                              and t->>'license_number' is not null
                        )::int
                        + exists (
                            select 1 from unnest(r.cities, r.states) as a (city, state)
                            where (n.practice_address->>'state' = a.state
                                and name_key(n.practice_address->>'city') = a.city)
                            or (n.mailing_address->>'state' = a.state
                                and name_key(n.mailing_address->>'city') = a.city)
                        )::int * 2
                        + coalesce(
                            left(name_key(n.middle_name), 1) = left(r.middle_key, 1),
                            false
                        )::int
                        + (n.status = 'active')::int
                        desc,
                        f.npi
                ) as position,
                {_CANDIDATE_COLUMNS:q}
            from (select distinct id, npi from hits) as f
                join recs r on r.id = f.id
                join npi_registry n on n.npi = f.npi
        )
        select * from ranked
        where position <= {CANDIDATE_CAP}
        order by record_id, position
    """


class _CandidateRow(NpiRow):
    """A mirror row found for ``record_id``, with how many rows were found for it."""

    record_id: UUID
    found: int


class _LinkRow(BaseModel):
    """One ``exclusion_npi_links`` row, mapped into an :class:`NpiLink` by ``to_domain``."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    record_id: UUID
    npi: str | None
    level: MatchLevel
    evidence: tuple[MatchEvidence, ...]
    explanation: str
    candidates: int
    mirror_snapshot_id: UUID
    linked_at: datetime

    def to_domain(self) -> NpiLink:
        return NpiLink(
            id=self.id,
            record_id=self.record_id,
            npi=self.npi,
            match=MatchResult(
                level=self.level, evidence=self.evidence, explanation=self.explanation
            ),
            candidates=self.candidates,
            mirror_snapshot_id=self.mirror_snapshot_id,
            linked_at=self.linked_at,
        )


class PostgresNpiLinkRepository:
    """Implements :class:`~credentialing.domain.ports.NpiLinkRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database
        self._records = PostgresExclusionRecordRepository(database)

    async def stale_records(
        self,
        *,
        source_id: SourceId | None,
        mirror_snapshot_id: UUID | None,
        limit: int | None = None,
    ) -> Sequence[UUID]:
        """Latest records without a link row made against ``mirror_snapshot_id``.

        A record's rows are written together under one mirror snapshot, so one row under
        another snapshot (or none) means all of them are stale.
        """
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select e.id from exclusion_records e
                where e.latest
                  and ({source_id}::text is null or e.source_id = {source_id})
                  and not exists (
                    select 1 from exclusion_npi_links l
                    where l.record_id = e.id
                      and l.mirror_snapshot_id is not distinct from {mirror_snapshot_id}::uuid
                  )
                order by e.id
                limit {limit}::bigint
                """
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def candidates(self, record_ids: Sequence[UUID]) -> Sequence[LinkCandidates]:
        """Each record's candidates in one discovery statement (module docstring).

        A person is looked up by last and first name key, by the initial of its first name
        on the mirror's side, and, when its first name is an initial, by the range of first
        names that initial starts; each alias as "Last, First ..." (with a comma) or as
        "First ... Last" and "Last First" (without). An organization by its name and each
        alias against the mirror's organization key and other-name keys. A record with a
        published NPI gets that NPI's row, listed in the latest file or not. The records
        themselves are read by the exclusion repository.
        """
        ids = sorted(set(record_ids))
        if not ids:
            return []
        records = await self._records.get_many(ids)
        recs = t"""
            select e.id, e.entity_kind, e.npi, e.aliases,
                name_key(e.last_name) as last_key,
                name_key(e.first_name) as first_key,
                name_key(e.middle_name) as middle_key,
                organization_key(e.organization_name) as org_key,
                array[substring(trim(e.address->>'postal_code') from '^[0-9]{{5}}')] as zips,
                array[name_key(e.address->>'city')] as cities,
                array[e.address->>'state'] as states,
                array[license_key(e.license_number)] as licenses,
                array[e.license_state] as license_states,
                array[e.license_state] as licensed_states
            from exclusion_records e
            where e.id = any({ids}::uuid[])
        """
        rows = await self._discover(recs)
        found: dict[UUID, list[NpiRecord]] = {}
        counts: dict[UUID, int] = {}
        for row in rows:
            found.setdefault(row.record_id, []).append(row.to_domain())
            counts[row.record_id] = row.found
        return [
            LinkCandidates(record, found.get(record.id, []), counts.get(record.id, 0))
            for record in sorted(records, key=lambda r: r.id)
        ]

    async def candidates_for(self, party: LinkParty) -> tuple[Sequence[NpiRecord], int]:
        """The party's candidates through the same discovery statement as :meth:`candidates`,
        its values given as parameters and keyed in SQL as a record's columns are; every
        licence and address of the party counts in the ranking. One INFO line per call: the
        party's kind, the count found and the time, never a name."""
        name = party.person_name
        places = party.addresses
        numbered = [item for item in party.licenses if item.number is not None]
        recs = t"""
            select {_PARTY_ID}::uuid as id, {party.entity_kind.value}::text as entity_kind,
                {party.npi}::char(10) as npi, {list(party.aliases)}::text[] as aliases,
                name_key({name.last if name else None}::text) as last_key,
                name_key({name.first if name else None}::text) as first_key,
                name_key({name.middle if name else None}::text) as middle_key,
                organization_key({party.organization_name}::text) as org_key,
                array(
                    select substring(trim(z) from '^[0-9]{{5}}')
                    from unnest({[a.postal_code for a in places]}::text[]) as z
                ) as zips,
                array(
                    select name_key(c) from unnest({[a.city for a in places]}::text[])
                        with ordinality as u (c, i)
                    order by i
                ) as cities,
                {[a.state for a in places]}::text[] as states,
                array(
                    select license_key(l)
                    from unnest({[item.number for item in numbered]}::text[])
                        with ordinality as u (l, i)
                    order by i
                ) as licenses,
                {[item.state for item in numbered]}::text[] as license_states,
                {[item.state for item in party.licenses]}::text[] as licensed_states
        """
        started = time.perf_counter()
        rows = await self._discover(recs)
        found = rows[0].found if rows else 0
        logger.info(
            "npi discovery party=%s found=%d elapsed_ms=%.0f",
            party.entity_kind.value,
            found,
            (time.perf_counter() - started) * 1000,
        )
        return [row.to_domain() for row in rows], found

    async def _discover(self, recs: Template) -> Sequence[_CandidateRow]:
        """Run the discovery statement over ``recs`` (:func:`_discovery`)."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_CandidateRow)) as cursor,
        ):
            await cursor.execute(_discovery(recs))
            return await cursor.fetchall()

    async def replace_links(self, links: Sequence[NpiLink]) -> None:
        """Delete the links of every record in ``links`` and copy these in, in one
        transaction. Rewrites are serialized by a transaction-scoped advisory lock, so two
        runs relinking the same record (a sync and ``link-npis``) cannot interleave."""
        if not links:
            return
        record_ids = sorted({link.record_id for link in links})
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(t"select pg_advisory_xact_lock({_LOCK_KEY})")
            await connection.execute(
                t"delete from exclusion_npi_links where record_id = any({record_ids}::uuid[])"
            )
            statement = sql.SQL("copy exclusion_npi_links ({columns}) from stdin").format(
                columns=sql.SQL(_LINK_COLUMNS)
            )
            async with connection.cursor() as cursor, cursor.copy(statement) as copy:
                for link in links:
                    match = link.match
                    await copy.write_row(
                        (
                            link.id,
                            link.record_id,
                            link.npi,
                            match.level.value,
                            Jsonb([item.model_dump(mode="json") for item in match.evidence]),
                            match.explanation,
                            link.candidates,
                            link.mirror_snapshot_id,
                            link.linked_at,
                        )
                    )

    async def links_for(self, record_ids: Sequence[UUID]) -> Mapping[UUID, Sequence[NpiLink]]:
        """Every link of these records, best level first, then by NPI."""
        ids = sorted(set(record_ids))
        if not ids:
            return {}
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_LinkRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                select {sql.SQL(_LINK_COLUMNS):q} from exclusion_npi_links
                where record_id = any({ids}::uuid[])
                order by record_id, array_position({_LEVELS}::text[], level), npi
                """
            )
            rows = await cursor.fetchall()
        links: dict[UUID, list[NpiLink]] = {}
        for row in rows:
            links.setdefault(row.record_id, []).append(row.to_domain())
        return links

    async def summary(self) -> NpiLinkSummary:
        """Latest records by their best link's level, and those never checked, in one pass."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select best, count(*) from (
                    select min(array_position({_LEVELS}::text[], l.level)) as best
                    from exclusion_records e
                        left join exclusion_npi_links l on l.record_id = e.id
                    where e.latest
                    group by e.id
                ) as records
                group by best
                """
            )
            rows = await cursor.fetchall()
        by_level = {MatchLevel(_LEVELS[best - 1]): count for best, count in rows if best}
        never = sum(count for best, count in rows if not best)
        return NpiLinkSummary(
            records=sum(count for _, count in rows),
            by_best_level=dict(sorted(by_level.items(), key=lambda item: level_rank(item[0]))),
            never_checked=never,
            last_run=await self.last_run(),
        )

    async def add_run(self, run: NpiLinkRun) -> None:
        """Store a finished run; ``phases`` as ``json`` so they keep their order."""
        dumped = run.model_dump(mode="json")
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                insert into npi_link_runs (
                    id, started_at, finished_at, trigger, mirror_snapshot_id, records, linked,
                    by_level, phases, cpu_time, note
                ) values (
                    {run.id}, {run.started_at}, {run.finished_at}, {run.trigger},
                    {run.mirror_snapshot_id}, {run.records}, {run.linked},
                    {Jsonb(dumped["by_level"])}, {Json(dumped["phases"])}, {run.cpu_time},
                    {run.note}
                )
                """
            )

    async def last_run(self) -> NpiLinkRun | None:
        """The most recently finished run, if any."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(NpiLinkRun)) as cursor,
        ):
            await cursor.execute(t"{_RUN_SELECT:q} order by finished_at desc, id limit 1")
            return await cursor.fetchone()
