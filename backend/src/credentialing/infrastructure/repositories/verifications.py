"""Verification history in Postgres: append-only and scoped by organization."""

from collections.abc import Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from psycopg import sql
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    MatchLevel,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import MatchEvidence, MatchResult, Provenance, VerificationResult
from credentialing.domain.types import SourceId
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import mapping_jsonb, models_jsonb

_SELECT: Final = sql.SQL(
    "select id, organization_id, provider_id, credential_type, source_id, outcome, finding,"
    " match_level, match_evidence, match_explanation, matched_record_ids, summary, details,"
    " source_url, method, verified_at, source_updated_at, snapshot_id, raw_record_ref,"
    " freshness, created_at from verifications"
)


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

    def to_domain(self) -> VerificationResult:
        match = (
            MatchResult(
                level=self.match_level,
                evidence=self.match_evidence or (),
                explanation=self.match_explanation or "",
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
        )


class PostgresVerificationRepository:
    """Implements :class:`~credentialing.domain.ports.VerificationRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def add(self, result: VerificationResult) -> None:
        """Append a verification result; the table rejects updates and deletes."""
        match = result.match
        provenance = result.provenance
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                insert into verifications (
                    id, organization_id, provider_id, credential_type, source_id, outcome,
                    finding, match_level, match_evidence, match_explanation, matched_record_ids,
                    summary, details, source_url, method, verified_at, source_updated_at,
                    snapshot_id, raw_record_ref, freshness, created_at
                ) values (
                    {result.id}, {result.organization_id}, {result.provider_id},
                    {result.credential_type.value}, {result.source_id}, {result.outcome.value},
                    {result.finding.value if result.finding else None},
                    {match.level.value if match else None},
                    {models_jsonb(match.evidence) if match else None},
                    {match.explanation if match else None},
                    {list(result.matched_record_ids)}::uuid[], {result.summary},
                    {mapping_jsonb(result.details)}, {provenance.source_url},
                    {provenance.method.value}, {provenance.verified_at},
                    {provenance.source_updated_at}, {provenance.snapshot_id},
                    {provenance.raw_record_ref}, {result.freshness.value}, {result.created_at}
                )
                """
            )

    async def latest(
        self,
        organization_id: UUID,
        provider_id: UUID,
        credential_type: CredentialType,
        source_id: SourceId,
    ) -> VerificationResult | None:
        """Return the most recent result for this provider, credential type and source."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where organization_id = {organization_id} and provider_id = {provider_id}
                  and credential_type = {credential_type.value} and source_id = {source_id}
                order by created_at desc, id desc limit 1
                """
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None

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

    async def history_all(
        self, organization_id: UUID, provider_id: UUID
    ) -> Sequence[VerificationResult]:
        """Return every result for this provider across credential types, newest first."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_VerificationRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q}
                where organization_id = {organization_id} and provider_id = {provider_id}
                order by created_at desc, id desc
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]
