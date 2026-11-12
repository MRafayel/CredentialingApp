from datetime import datetime, timedelta
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
    MatchRelation,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    HistoryPage,
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


def _at(
    result: VerificationResult, at: datetime, *, source_id: str | None = None, new_id: bool = True
) -> VerificationResult:
    """The result stored (and verified) at ``at``, under a new id unless told otherwise."""
    source = source_id or result.source_id
    provenance = result.provenance.model_copy(update={"verified_at": at, "source_id": source})
    return result.model_copy(
        update={
            "id": uuid4() if new_id else result.id,
            "created_at": at,
            "source_id": source,
            "provenance": provenance,
        }
    )


def _npi(result: VerificationResult, source_id: str = "test_npi_source") -> VerificationResult:
    provenance = result.provenance.model_copy(update={"source_id": source_id})
    return result.model_copy(
        update={
            "credential_type": CredentialType.NPI,
            "source_id": source_id,
            "provenance": provenance,
        }
    )


async def test_history_page_returns_whole_moments_newest_first(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    other_organization, _ = await _provider(database, db_connection)
    run_one = [_result(organization_id, provider_id, source_id=s) for s in ("test_a", "test_b")]
    npi = _npi(_result(organization_id, provider_id, minutes_later=5))
    run_two = [
        _result(organization_id, provider_id, minutes_later=9, source_id=s)
        for s in ("test_a", "test_b")
    ]
    await repository.add_many([*run_one, npi, *run_two])

    first = await repository.history_page(organization_id, provider_id, before=None, moments=2)
    second = await repository.history_page(
        organization_id, provider_id, before=first.next_before, moments=2
    )
    one = await repository.history_page(organization_id, provider_id, before=None, moments=1)

    assert {r.id for r in first.results[:2]} == {r.id for r in run_two}
    assert first.results[2:] == (npi,)
    assert first.next_before == npi.created_at
    assert {r.id for r in second.results} == {r.id for r in run_one}
    assert second.next_before is None
    # A check run's results share one instant, so a page never splits them.
    assert {r.id for r in one.results} == {r.id for r in run_two}
    assert one.next_before == run_two[0].created_at
    assert await repository.history_page(
        other_organization, provider_id, before=None, moments=2
    ) == HistoryPage(results=())


async def test_latest_npi_is_the_newest_npi_result_of_any_source(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    other_organization, _ = await _provider(database, db_connection)
    assert await repository.latest_npi(organization_id, provider_id) is None
    mirror = _npi(_result(organization_id, provider_id), "test_mirror")
    live = _npi(_result(organization_id, provider_id, minutes_later=3), "test_live")
    newer_list = _result(organization_id, provider_id, minutes_later=9)
    await repository.add_many([mirror, live, newer_list])

    assert await repository.latest_npi(organization_id, provider_id) == live
    assert await repository.latest_npi(other_organization, provider_id) is None


async def test_purge_keeps_each_sources_newest_results_and_other_organizations(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    other_organization, other_provider = await _provider(database, db_connection)
    cutoff = NOW
    base = _result(organization_id, provider_id)
    oldest = _at(base, cutoff - timedelta(days=200))
    last_answer = _at(base, cutoff - timedelta(days=190))
    blocked = _at(
        _result(organization_id, provider_id, outcome=Outcome.BLOCKED),
        cutoff - timedelta(days=185),
    )
    lone = _at(base, cutoff - timedelta(days=400), source_id="test_lone")
    old_npi = _at(_npi(base), cutoff - timedelta(days=300))
    newest_npi = _at(_npi(base), cutoff - timedelta(days=250))
    recent = _at(base, cutoff + timedelta(minutes=1), source_id="test_recent")
    old_recent = _at(base, cutoff - timedelta(days=199), source_id="test_recent")
    theirs = _at(_result(other_organization, other_provider), cutoff - timedelta(days=300))
    theirs_newer = _at(_result(other_organization, other_provider), cutoff - timedelta(days=200))
    await repository.add_many(
        [oldest, last_answer, blocked, lone, old_npi, newest_npi, recent, old_recent]
    )
    await repository.add_many([theirs, theirs_newer])

    purged = await repository.purge(organization_id, before=cutoff)

    assert purged == 3
    exclusions = await repository.history(organization_id, provider_id, CredentialType.EXCLUSION)
    # The newest result of each list stays however old, and so does the last answer
    # (completed) change detection compares the next one with.
    assert {r.id for r in exclusions} == {last_answer.id, blocked.id, lone.id, recent.id}
    assert await repository.history(organization_id, provider_id, CredentialType.NPI) == [
        newest_npi
    ]
    assert await repository.latest_npi(organization_id, provider_id) == newest_npi
    assert {
        r.id
        for r in await repository.history(
            other_organization, other_provider, CredentialType.EXCLUSION
        )
    } == {theirs.id, theirs_newer.id}
    assert await repository.purge(organization_id, before=cutoff) == 0


async def test_only_the_purge_may_delete_and_nothing_may_update(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    old = _at(_result(organization_id, provider_id), NOW - timedelta(days=200))
    new = _result(organization_id, provider_id, minutes_later=1)
    await repository.add_many([old, new])

    # Before any purge in this transaction: a plain delete is rejected by the trigger.
    with pytest.raises(RaiseException, match="append-only: DELETE"):
        async with db_connection.transaction():
            await db_connection.execute(t"delete from verifications where id = {old.id}")
    assert await repository.purge(organization_id, before=NOW) == 1
    with pytest.raises(RaiseException, match="append-only: UPDATE"):
        async with db_connection.transaction():
            await db_connection.execute(
                t"update verifications set summary = 'changed' where id = {new.id}"
            )
    assert await repository.history(organization_id, provider_id, CredentialType.EXCLUSION) == [new]


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


async def test_the_match_relation_round_trips_and_a_missing_one_is_the_party(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresVerificationRepository(database)
    organization_id, provider_id = await _provider(database, db_connection)
    stored = _result(organization_id, provider_id)
    assert stored.match is not None
    related = stored.model_copy(
        update={
            "finding": Finding.RELATED_PARTY,
            "match": stored.match.model_copy(update={"relation": MatchRelation.RELATED_PARTY}),
        }
    )
    await repository.add(related)
    # A row stored before the relation existed has none: copy the row without it.
    await db_connection.execute(
        t"""
        insert into verifications (
            id, organization_id, provider_id, credential_type, source_id, outcome, finding,
            match_level, match_evidence, match_explanation, summary, source_url, method,
            verified_at, freshness, created_at
        )
        select {uuid4()}, organization_id, provider_id, credential_type, 'test_older', outcome,
               'record_found', match_level, match_evidence, match_explanation, summary,
               source_url, method, verified_at, freshness, created_at
        from verifications where id = {related.id}
        """
    )

    by_source = {
        r.source_id: r
        for r in await repository.latest_per_source(
            organization_id, provider_id, CredentialType.EXCLUSION
        )
    }

    assert by_source[SOURCE] == related
    read_older = by_source["test_older"].match
    assert read_older is not None
    assert read_older.relation is MatchRelation.PARTY
