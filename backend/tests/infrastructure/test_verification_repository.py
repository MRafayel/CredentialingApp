from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ForeignKeyViolation, RaiseException
from psycopg.rows import TupleRow

from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    MatchField,
    MatchLevel,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    MatchEvidence,
    MatchResult,
    Provenance,
    VerificationResult,
)
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.providers import PostgresProviderRepository
from credentialing.infrastructure.repositories.verifications import (
    PostgresVerificationRepository,
)
from tests.synthetic import NOW, individual

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_exclusions"


async def _provider(
    database: ConnectionSource, connection: AsyncConnection[TupleRow]
) -> tuple[UUID, UUID]:
    organization_id = uuid4()
    await connection.execute(
        t"insert into organizations (id, name) values ({organization_id}, 'Testorg Clinic')"
    )
    provider = individual(organization_id=organization_id)
    await PostgresProviderRepository(database).add(provider)
    return organization_id, provider.id


def _result(
    organization_id: UUID,
    provider_id: UUID,
    *,
    minutes_later: int = 0,
    source_id: str = SOURCE,
    outcome: Outcome = Outcome.COMPLETED,
) -> VerificationResult:
    at = NOW + timedelta(minutes=minutes_later)
    completed = outcome is Outcome.COMPLETED
    return VerificationResult(
        id=uuid4(),
        organization_id=organization_id,
        provider_id=provider_id,
        credential_type=CredentialType.EXCLUSION,
        source_id=source_id,
        outcome=outcome,
        finding=Finding.RECORD_FOUND if completed else None,
        match=MatchResult(
            level=MatchLevel.POTENTIAL,
            evidence=(
                MatchEvidence(field=MatchField.LAST_NAME, matched=True),
                MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail="alias"),
            ),
            explanation="Potential match: last name matches, first name matches (alias).",
        )
        if completed
        else None,
        matched_record_ids=(uuid4(),) if completed else (),
        summary="synthetic result",
        details={"list": "synthetic"},
        provenance=Provenance(
            source_id=source_id,
            source_url="https://example.test/list.tsv",
            method=VerificationMethod.BULK_DATASET,
            verified_at=at,
            source_updated_at=at - timedelta(days=2),
            raw_record_ref="row:17",
        ),
        freshness=Freshness.LIVE,
        created_at=at,
    )


async def test_add_then_latest_round_trips(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    earlier = _result(organization_id, provider_id)
    later = _result(organization_id, provider_id, minutes_later=5, outcome=Outcome.BLOCKED)
    other_source = _result(organization_id, provider_id, minutes_later=9, source_id="test_other")
    for result in (earlier, later, other_source):
        await repository.add(result)

    latest = await repository.latest(organization_id, provider_id, CredentialType.EXCLUSION, SOURCE)

    assert latest == later
    assert latest is not None
    assert latest.finding is None


async def test_history_is_newest_first_and_scoped_by_organization(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    other_organization, _ = await _provider(database, db_connection)
    first = _result(organization_id, provider_id)
    second = _result(organization_id, provider_id, minutes_later=5)
    await repository.add(first)
    await repository.add(second)

    history = await repository.history(organization_id, provider_id, CredentialType.EXCLUSION)

    assert history == [second, first]
    assert await repository.history(other_organization, provider_id, CredentialType.EXCLUSION) == []
    assert (
        await repository.latest(other_organization, provider_id, CredentialType.EXCLUSION, SOURCE)
        is None
    )


async def test_history_is_append_only(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    result = _result(organization_id, provider_id)
    await repository.add(result)

    with pytest.raises(RaiseException, match="append-only: UPDATE"):
        async with db_connection.transaction():
            await db_connection.execute(
                t"update verifications set summary = 'changed' where id = {result.id}"
            )
    with pytest.raises(RaiseException, match="append-only: DELETE"):
        async with db_connection.transaction():
            await db_connection.execute(t"delete from verifications where id = {result.id}")

    assert await repository.history(organization_id, provider_id, CredentialType.EXCLUSION) == [
        result
    ]


async def test_a_result_cannot_point_at_another_organizations_provider(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    _, provider_id = await _provider(database, db_connection)
    other_organization, _ = await _provider(database, db_connection)

    with pytest.raises(ForeignKeyViolation):
        await repository.add(_result(other_organization, provider_id))


async def test_schema_rejects_a_finding_on_a_check_that_could_not_run(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    organization_id, provider_id = await _provider(database, db_connection)
    blocked = _result(organization_id, provider_id, outcome=Outcome.BLOCKED)
    await PostgresVerificationRepository(database).add(blocked)

    with pytest.raises(CheckViolation):
        async with db_connection.transaction():
            await db_connection.execute(
                t"""
                insert into verifications (
                    id, organization_id, provider_id, credential_type, source_id, outcome,
                    finding, summary, source_url, method, verified_at, freshness, created_at
                )
                select {uuid4()}, organization_id, provider_id, credential_type, source_id,
                       outcome, 'no_record', summary, source_url, method, verified_at,
                       freshness, created_at
                from verifications where id = {blocked.id}
                """
            )


async def test_history_all_spans_credential_types_newest_first(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    exclusion_result = _result(organization_id, provider_id)
    license_result = _result(organization_id, provider_id, minutes_later=3).model_copy(
        update={"credential_type": CredentialType.STATE_LICENSE}
    )
    for result in (exclusion_result, license_result):
        await repository.add(result)

    history = await repository.history_all(organization_id, provider_id)

    assert history == [license_result, exclusion_result]
    assert await repository.history_all(uuid4(), provider_id) == []


async def test_latest_per_source_keeps_the_newest_result_of_each_source(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    other_organization, _ = await _provider(database, db_connection)
    old = _result(organization_id, provider_id)
    new = _result(organization_id, provider_id, minutes_later=5, outcome=Outcome.BLOCKED)
    other = _result(organization_id, provider_id, minutes_later=1, source_id="test_another")
    licence = _result(organization_id, provider_id, minutes_later=9).model_copy(
        update={"credential_type": CredentialType.STATE_LICENSE}
    )
    tied = [
        _result(organization_id, provider_id, minutes_later=2, source_id="test_tied").model_copy(
            update={"id": UUID(int=index)}
        )
        for index in (1, 2)
    ]
    for result in (old, new, other, licence, *tied):
        await repository.add(result)

    latest = await repository.latest_per_source(
        organization_id, provider_id, CredentialType.EXCLUSION
    )

    assert latest == [other, new, tied[1]]
    assert (
        await repository.latest_per_source(
            other_organization, provider_id, CredentialType.EXCLUSION
        )
        == []
    )
