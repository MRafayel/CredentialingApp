"""Verification history in Postgres: append-only (the retention purge is the one delete) and
scoped by organization."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.rows import TupleRow, class_row
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    MatchLevel,
    MatchRelation,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    HistoryPage,
    MatchEvidence,
    MatchResult,
    Provenance,
    ResultChange,
    VerificationResult,
)
from credentialing.domain.types import SourceId
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import mapping_jsonb, model_jsonb, models_jsonb

_COLUMNS: Final = sql.SQL(
    "id, organization_id, provider_id, credential_type, source_id, outcome, finding,"
    " match_level, match_evidence, match_explanation, match_relation, matched_record_ids,"
    " summary, details, source_url, method, verified_at, source_updated_at, snapshot_id,"
    " raw_record_ref, freshness, created_at, changed_from"
)
_SELECT: Final = sql.SQL("select {} from verifications").format(_COLUMNS)


class _VerificationRow(BaseModel):
    """One ``verifications`` row, mapped into a :class:`VerificationResult` by :meth:`to_domain`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    organization_id: UUID
    provider_id: UUID
    credential_type: CredentialType
    source_id: str
    outcome: Outcome
    finding: Finding | None
    match_level: MatchLevel | None
    match_evidence: tuple[MatchEvidence, ...] | None
    match_explanation: str | None
    match_relation: MatchRelation | None
    """Null on rows stored before the relation existed; those were matched as the party."""
    matched_record_ids: tuple[UUID, ...]
    summary: str
    details: dict[str, str]
    source_url: str
    method: VerificationMethod
    verified_at: datetime
    source_updated_at: datetime | None
    snapshot_id: UUID | None
    raw_record_ref: str | None
    freshness: Freshness
    created_at: datetime
    changed_from: ResultChange | None

    def to_domain(self) -> VerificationResult:
        match = (
            MatchResult(
                level=self.match_level,
                evidence=self.match_evidence or (),
                explanation=self.match_explanation or "",
                relation=self.match_relation or MatchRelation.PARTY,
            )
            if self.match_level is not None
            else None
        )
        return VerificationResult(
            id=self.id,
            organization_id=self.organization_id,
            provider_id=self.provider_id,
            credential_type=self.credential_type,
            source_id=self.source_id,
            outcome=self.outcome,
            finding=self.finding,
            match=match,
            matched_record_ids=self.matched_record_ids,
            summary=self.summary,
            details=self.details,
            provenance=Provenance(
                source_id=self.source_id,
                source_url=self.source_url,
                method=self.method,
                verified_at=self.verified_at,
                source_updated_at=self.source_updated_at,
                snapshot_id=self.snapshot_id,
                raw_record_ref=self.raw_record_ref,
            ),
            freshness=self.freshness,
            created_at=self.created_at,
            changed_from=self.changed_from,
        )


class PostgresVerificationRepository:
    """Implements :class:`~credentialing.domain.ports.VerificationRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def add(self, result: VerificationResult) -> None:
        """Append a verification result; the table rejects updates and deletes."""
        async with self._database.connection() as connection, connection.transaction():
            await _insert(connection, result)

    async def add_many(self, results: Sequence[VerificationResult]) -> None:
        """Append several results in one transaction, pipelined."""
        if not results:
            return
        async with (
            self._database.connection() as connection,
            connection.transaction(),
            connection.pipeline(),
        ):
            for result in results:
                await _insert(connection, result)

    async def latest_per_source_many(
        self, organization_id: UUID, provider_ids: Sequence[UUID], credential_type: CredentialType
    ) -> Mapping[UUID, Sequence[VerificationResult]]:
        """The most recent result of each source for each provider, in one query (the
        ``verifications_latest_idx`` per provider), each provider's ordered by source id."""
        if not provider_ids:
            return {}
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                select distinct on (provider_id, source_id) {_COLUMNS:q} from verifications
                where organization_id = {organization_id}
                  and provider_id = any({list(provider_ids)}::uuid[])
                  and credential_type = {credential_type.value}
                order by provider_id, source_id, created_at desc, id desc
                """
            )
            rows = await cursor.fetchall()
        latest: dict[UUID, list[VerificationResult]] = {}
        for row in rows:
            latest.setdefault(row.provider_id, []).append(row.to_domain())
        return latest

    async def last_changes(
        self, organization_id: UUID, provider_ids: Sequence[UUID]
    ) -> Mapping[UUID, datetime]:
        """The newest changed result's time per provider, through ``verifications_changed_idx``."""
        if not provider_ids:
            return {}
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select provider_id, max(created_at) from verifications
                where organization_id = {organization_id}
                  and provider_id = any({list(provider_ids)}::uuid[])
                  and changed_from is not null
                group by provider_id
                """
            )
            rows = await cursor.fetchall()
        return dict(rows)

    async def latest(
        self,
        organization_id: UUID,
        provider_id: UUID,
        credential_type: CredentialType,
        source_id: SourceId,
        *,
        completed_only: bool = False,
    ) -> VerificationResult | None:
        """Return the most recent result for this provider, credential type and source (only
        ``COMPLETED`` ones with ``completed_only``)."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where organization_id = {organization_id} and provider_id = {provider_id}
                  and credential_type = {credential_type.value} and source_id = {source_id}
                  and ({not completed_only} or outcome = {Outcome.COMPLETED.value})
                order by created_at desc, id desc limit 1
                """
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def latest_per_source(
        self,
        organization_id: UUID,
        provider_id: UUID,
        credential_type: CredentialType,
        *,
        completed_only: bool = False,
    ) -> Sequence[VerificationResult]:
        """Return the most recent result of each source, ordered by source id, in one query
        (only ``COMPLETED`` ones with ``completed_only``)."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                select distinct on (source_id) {_COLUMNS:q} from verifications
                where organization_id = {organization_id} and provider_id = {provider_id}
                  and credential_type = {credential_type.value}
                  and ({not completed_only} or outcome = {Outcome.COMPLETED.value})
                order by source_id, created_at desc, id desc
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def history(
        self, organization_id: UUID, provider_id: UUID, credential_type: CredentialType
    ) -> Sequence[VerificationResult]:
        """Return every result for this provider and credential type, newest first."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where organization_id = {organization_id} and provider_id = {provider_id}
                  and credential_type = {credential_type.value}
                order by created_at desc, id desc
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def history_page(
        self, organization_id: UUID, provider_id: UUID, *, before: datetime | None, moments: int
    ) -> HistoryPage:
        """The results of the newest ``moments`` instants older than ``before``, in one
        statement: the instants are the provider's distinct ``created_at`` values, one more
        than asked for read only to say whether older ones exist."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_PagedRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                with moments as (
                    select created_at, row_number() over (order by created_at desc) as n
                    from (
                        select distinct created_at from verifications
                        where organization_id = {organization_id}
                          and provider_id = {provider_id}
                          and ({before}::timestamptz is null or created_at < {before})
                        order by created_at desc
                        limit {moments + 1}
                    ) as instants
                )
                select {_COLUMNS:q}, (select count(*) from moments) > {moments} as more
                from verifications
                where organization_id = {organization_id} and provider_id = {provider_id}
                  and created_at in (select created_at from moments where n <= {moments})
                order by created_at desc, id desc
                """
            )
            rows = await cursor.fetchall()
        results = tuple(row.to_domain() for row in rows)
        more = bool(rows) and rows[0].more
        return HistoryPage(results=results, next_before=results[-1].created_at if more else None)

    async def latest_npi(
        self, organization_id: UUID, provider_id: UUID
    ) -> VerificationResult | None:
        """The provider's newest NPI result from any source (``verifications_latest_idx``)."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where organization_id = {organization_id} and provider_id = {provider_id}
                  and credential_type = {CredentialType.NPI.value}
                order by created_at desc, id desc limit 1
                """
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def purge(self, organization_id: UUID, *, before: datetime) -> int:
        """Delete the organization's results stored before ``before`` in one transaction.

        Kept whatever their age, per provider: the newest result of each credential type and
        source (the verdict reads it) and the newest completed one (change detection compares
        with it); the newest NPI result of any source, completed or not, is among them. The
        append-only trigger lets this delete through only because the transaction-local
        ``credentialing.history_purge`` setting is on; it is set right before the delete.
        """
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute("select set_config('credentialing.history_purge', 'on', true)")
            cursor = await connection.execute(
                t"""
                with kept as (
                    (select distinct on (provider_id, credential_type, source_id) id
                     from verifications where organization_id = {organization_id}
                     order by provider_id, credential_type, source_id, created_at desc, id desc)
                    union all
                    (select distinct on (provider_id, credential_type, source_id) id
                     from verifications
                     where organization_id = {organization_id}
                       and outcome = {Outcome.COMPLETED.value}
                     order by provider_id, credential_type, source_id, created_at desc, id desc)
                )
                delete from verifications
                where organization_id = {organization_id} and created_at < {before}
                  and id not in (select id from kept)
                """
            )
            return cursor.rowcount


class _PagedRow(_VerificationRow):
    """A row of :meth:`PostgresVerificationRepository.history_page`, with whether instants
    older than the page exist."""

    more: bool


async def _insert(connection: AsyncConnection[TupleRow], result: VerificationResult) -> None:
    """One result's insert, on the caller's connection and transaction."""
    match = result.match
    provenance = result.provenance
    await connection.execute(
        t"""
        insert into verifications (
            id, organization_id, provider_id, credential_type, source_id, outcome,
            finding, match_level, match_evidence, match_explanation, match_relation,
            matched_record_ids, summary, details, source_url, method, verified_at,
            source_updated_at, snapshot_id, raw_record_ref, freshness, created_at, changed_from
        ) values (
            {result.id}, {result.organization_id}, {result.provider_id},
            {result.credential_type.value}, {result.source_id}, {result.outcome.value},
            {result.finding.value if result.finding else None},
            {match.level.value if match else None},
            {models_jsonb(match.evidence) if match else None},
            {match.explanation if match else None},
            {match.relation.value if match else None},
            {list(result.matched_record_ids)}::uuid[], {result.summary},
            {mapping_jsonb(result.details)}, {provenance.source_url},
            {provenance.method.value}, {provenance.verified_at},
            {provenance.source_updated_at}, {provenance.snapshot_id},
            {provenance.raw_record_ref}, {result.freshness.value}, {result.created_at},
            {model_jsonb(result.changed_from) if result.changed_from else None}
        )
        """
    )
