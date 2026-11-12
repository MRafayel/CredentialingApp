"""Monitoring storage against the local database: statuses, roles, changes and runs, and the
tenant rule that organization A's calls never return or touch organization B's rows."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation
from psycopg.rows import TupleRow

from credentialing.bootstrap import Services, build_services
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    Attention,
    AttentionScope,
    CredentialType,
    Finding,
    Freshness,
    IdentityState,
    Outcome,
    RecordStatus,
    RosterRole,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    FetchResult,
    MonitoringRun,
    Provenance,
    Provider,
    ProviderStatus,
    ResultChange,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.policies import STATUS_RULES_VERSION
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.monitoring_runs import (
    PostgresMonitoringRunRepository,
)
from credentialing.infrastructure.repositories.providers import PostgresProviderRepository
from credentialing.infrastructure.repositories.verifications import (
    PostgresVerificationRepository,
)
from tests.fakes import FixedClock, ScriptedConnector, capabilities
from tests.synthetic import NOW, exclusion, individual

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_monitoring_list"


async def _organization(connection: AsyncConnection[TupleRow]) -> UUID:
    organization_id = uuid4()
    await connection.execute(
        t"insert into organizations (id, name) values ({organization_id}, 'Testorg Clinic')"
    )
    return organization_id


def _result(
    provider: Provider,
    finding: Finding = Finding.NO_RECORD,
    *,
    minutes: int = 0,
    changed: bool = False,
) -> VerificationResult:
    at = NOW + timedelta(minutes=minutes)
    return VerificationResult(
        id=uuid4(),
        organization_id=provider.organization_id,
        provider_id=provider.id,
        credential_type=CredentialType.EXCLUSION,
        source_id=SOURCE,
        outcome=Outcome.COMPLETED,
        finding=finding,
        summary="synthetic result",
        details={"record_status": "active"} if finding is Finding.RECORD_FOUND else {},
        provenance=Provenance(
            source_id=SOURCE,
            source_url="https://example.test/list",
            method=VerificationMethod.BULK_DATASET,
            verified_at=at,
        ),
        freshness=Freshness.CACHED,
        created_at=at,
        changed_from=(
            ResultChange(
                outcome=Outcome.COMPLETED,
                finding=Finding.NO_RECORD,
                record_status=RecordStatus.ACTIVE,
                verified_at=at - timedelta(days=15),
            )
            if changed
            else None
        ),
    )


async def _history(
    services: Services, organization_id: UUID, provider_id: UUID
) -> tuple[VerificationResult, ...]:
    """The provider's whole (short) history, newest first."""
    page = await services.verifications.history_page(
        organization_id, provider_id, before=None, moments=50
    )
    return page.results


def _status(provider: Provider, attention: Attention | None) -> ProviderStatus:
    """A stored status; a listed one changed a day before ``NOW``, so ``changed_at`` round-trips."""
    return ProviderStatus(
        provider_id=provider.id,
        verdict=Verdict.LISTED if attention is Attention.LISTED else Verdict.NO_RECORDS,
        identity=IdentityState.NOT_EXPECTED,
        changed=False,
        attention=attention,
        last_checked_at=NOW,
        next_check_at=NOW + timedelta(days=15),
        listed_in=(SOURCE,) if attention is Attention.LISTED else (),
        changed_at=NOW - timedelta(days=1) if attention is Attention.LISTED else None,
    )


class _Tenants:
    """Organization A with two providers, organization B with one, every row stored."""

    def __init__(self, database: ConnectionSource) -> None:
        self.providers = PostgresProviderRepository(database)
        self.verifications = PostgresVerificationRepository(database)

    async def build(self, connection: AsyncConnection[TupleRow]) -> None:
        self.a = await _organization(connection)
        self.b = await _organization(connection)
        self.a_listed = individual(last="Listperson", organization_id=self.a).model_copy(
            update={"role": RosterRole.WORKFORCE, "created_at": NOW - timedelta(days=1)}
        )
        self.a_clear = individual(last="Clearperson", organization_id=self.a)
        self.b_listed = individual(last="Otherperson", organization_id=self.b)
        for provider in (self.a_listed, self.a_clear, self.b_listed):
            await self.providers.add(provider)
        await self.verifications.add_many(
            [
                _result(self.a_listed, Finding.RECORD_FOUND, changed=True),
                _result(self.a_clear),
                _result(self.b_listed, Finding.RECORD_FOUND, changed=True),
            ]
        )
        await self.providers.save_statuses(
            self.a,
            [_status(self.a_listed, Attention.LISTED), _status(self.a_clear, None)],
            rules_version=STATUS_RULES_VERSION,
        )
        await self.providers.save_statuses(
            self.b, [_status(self.b_listed, Attention.LISTED)], rules_version=STATUS_RULES_VERSION
        )


@pytest.fixture
async def tenants(database: ConnectionSource, db_connection: AsyncConnection[TupleRow]) -> _Tenants:
    world = _Tenants(database)
    await world.build(db_connection)
    return world


async def test_role_and_last_viewed_round_trip(tenants: _Tenants) -> None:
    stored = await tenants.providers.get(tenants.a, tenants.a_listed.id)

    assert stored == tenants.a_listed
    assert stored is not None
    assert (stored.role, stored.last_viewed_at) == (RosterRole.WORKFORCE, None)


async def test_a_page_lists_one_organization_attention_first(tenants: _Tenants) -> None:
    rows, total = await tenants.providers.list_status(
        tenants.a, query=None, attention=None, limit=10, offset=0
    )
    listed, total_listed = await tenants.providers.list_status(
        tenants.a, query=None, attention=Attention.LISTED, limit=10, offset=0
    )
    searched, _ = await tenants.providers.list_status(
        tenants.a, query="clear", attention=None, limit=10, offset=0
    )

    assert total == 2
    assert [(p.id, s.attention if s else "?") for p, s in rows] == [
        (tenants.a_listed.id, Attention.LISTED),
        (tenants.a_clear.id, None),
    ]
    assert rows[0][1] == _status(tenants.a_listed, Attention.LISTED)
    assert ([p.id for p, _ in listed], total_listed) == ([tenants.a_listed.id], 1)
    assert [p.id for p, _ in searched] == [tenants.a_clear.id]


async def test_a_page_keeps_any_attention_or_none_within_one_organization(
    tenants: _Tenants,
) -> None:
    some, some_total = await tenants.providers.list_status(
        tenants.a, query=None, attention=AttentionScope.ANY, limit=10, offset=0
    )
    none, none_total = await tenants.providers.list_status(
        tenants.a, query=None, attention=AttentionScope.NONE, limit=10, offset=0
    )
    theirs, _ = await tenants.providers.list_status(
        tenants.b, query=None, attention=AttentionScope.NONE, limit=10, offset=0
    )

    assert ([p.id for p, _ in some], some_total) == ([tenants.a_listed.id], 1)
    assert ([p.id for p, _ in none], none_total) == ([tenants.a_clear.id], 1)
    assert list(theirs) == []


async def test_counts_are_one_organizations(tenants: _Tenants) -> None:
    assert await tenants.providers.status_counts(tenants.a) == {Attention.LISTED: 1, None: 1}
    assert await tenants.providers.status_counts(tenants.b) == {Attention.LISTED: 1}


async def test_a_status_is_never_written_under_another_organization(
    tenants: _Tenants, db_connection: AsyncConnection[TupleRow]
) -> None:
    # B's provider with a status row: A's write leaves it as it was.
    await tenants.providers.save_statuses(
        tenants.a, [_status(tenants.b_listed, None)], rules_version=STATUS_RULES_VERSION
    )
    assert await tenants.providers.status_counts(tenants.b) == {Attention.LISTED: 1}
    # A provider of B without a status row: A's write is refused by the foreign key.
    other = individual(last="Newperson", organization_id=tenants.b)
    await tenants.providers.add(other)
    with pytest.raises(ForeignKeyViolation):
        await tenants.providers.save_statuses(
            tenants.a, [_status(other, None)], rules_version=STATUS_RULES_VERSION
        )
    cursor = await db_connection.execute(
        t"select count(*) from provider_status where provider_id = {other.id}"
    )
    assert await cursor.fetchone() == (0,)


async def test_stale_statuses_are_one_organizations_missing_or_older_rows(
    tenants: _Tenants,
) -> None:
    newcomer = individual(last="Newperson", organization_id=tenants.a)
    await tenants.providers.add(newcomer)
    await tenants.providers.save_statuses(
        tenants.a, [_status(tenants.a_clear, None)], rules_version=STATUS_RULES_VERSION - 1
    )

    stale = await tenants.providers.stale_statuses(tenants.a, STATUS_RULES_VERSION)

    assert {p.id for p in stale} == {tenants.a_clear.id, newcomer.id}
    assert await tenants.providers.stale_statuses(tenants.b, STATUS_RULES_VERSION) == []


async def test_marking_viewed_touches_only_the_callers_provider(tenants: _Tenants) -> None:
    at = NOW + timedelta(hours=2)

    viewed = await tenants.providers.mark_viewed(tenants.a, tenants.a_clear.id, at)
    refused = await tenants.providers.mark_viewed(tenants.a, tenants.b_listed.id, at)

    assert viewed is not None
    assert viewed.last_viewed_at == at
    assert refused is None
    untouched = await tenants.providers.get(tenants.b, tenants.b_listed.id)
    assert untouched is not None
    assert untouched.last_viewed_at is None


async def test_the_system_job_reads_one_organization_at_a_time(tenants: _Tenants) -> None:
    organizations = await tenants.providers.organization_ids()
    roster_a = await tenants.providers.list_all(tenants.a)
    roster_b = await tenants.providers.list_all(tenants.b)

    assert {tenants.a, tenants.b} <= set(organizations)
    assert [p.id for p, _ in roster_a] == [tenants.a_listed.id, tenants.a_clear.id]
    assert [(p.id, s is not None) for p, s in roster_b] == [(tenants.b_listed.id, True)]


async def test_latest_results_and_changes_are_one_organizations(tenants: _Tenants) -> None:
    ids = [tenants.a_listed.id, tenants.a_clear.id, tenants.b_listed.id]

    latest = await tenants.verifications.latest_per_source_many(
        tenants.a, ids, CredentialType.EXCLUSION
    )
    changes = await tenants.verifications.last_changes(tenants.a, ids)
    b_latest = await tenants.verifications.latest_per_source(
        tenants.a, tenants.b_listed.id, CredentialType.EXCLUSION
    )

    assert set(latest) == {tenants.a_listed.id, tenants.a_clear.id}
    assert set(changes) == {tenants.a_listed.id}
    assert b_latest == []
    (stored,) = latest[tenants.a_listed.id]
    assert stored.changed_from is not None
    assert stored.changed_from.record_status is RecordStatus.ACTIVE


async def test_the_newest_change_and_newest_result_per_list_win(tenants: _Tenants) -> None:
    later = _result(tenants.a_clear, minutes=30, changed=True)
    latest_clear = _result(tenants.a_clear, minutes=60)
    await tenants.verifications.add_many([later, latest_clear])

    latest = await tenants.verifications.latest_per_source_many(
        tenants.a, [tenants.a_clear.id], CredentialType.EXCLUSION
    )
    changes = await tenants.verifications.last_changes(tenants.a, [tenants.a_clear.id])

    assert latest == {tenants.a_clear.id: [latest_clear]}
    assert changes == {tenants.a_clear.id: later.created_at}
    history = await tenants.verifications.history(
        tenants.a, tenants.a_clear.id, CredentialType.EXCLUSION
    )
    assert history[1] == later


async def test_runs_round_trip_newest_first(database: ConnectionSource) -> None:
    runs = PostgresMonitoringRunRepository(database)
    older = MonitoringRun(
        id=uuid4(),
        started_at=NOW + timedelta(days=400),
        finished_at=NOW + timedelta(days=400, minutes=1),
        trigger="due",
        organizations=2,
        providers=3,
        results=8,
        changed=1,
        failures=0,
        purged=310,
        phases={"providers": timedelta(seconds=1), "exclusions": timedelta(seconds=40)},
        cpu_time=timedelta(seconds=12.5),
    )
    newer = older.model_copy(
        update={
            "id": uuid4(),
            "finished_at": NOW + timedelta(days=401),
            "trigger": "list_sync:test_monitoring_list",
            "source_id": SOURCE,
            "phases": {},
            "cpu_time": None,
            "purged": 0,
            "note": "no provider was due; nothing was checked",
        }
    )

    await runs.add_run(older)
    await runs.add_run(newer)

    assert await runs.last_run() == newer
    stored = await runs.runs(2)
    assert stored == [newer, older]
    assert list(stored[1].phases) == ["providers", "exclusions"]


async def test_the_job_appends_results_only_under_each_providers_organization(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
        content_hash="ab" * 32,
    )
    registry = ConnectorRegistry()
    registry.register(
        ScriptedConnector(
            capabilities(SOURCE),
            FetchResult(
                snapshot=snapshot,
                records=(exclusion(source_id=SOURCE, snapshot_id=snapshot.id, last="Jobperson"),),
            ),
        )
    )
    clock = FixedClock(NOW + timedelta(days=500))
    services = build_services(database, registry, clock)
    a, b = await _organization(db_connection), await _organization(db_connection)
    in_a = individual(last="Jobperson", organization_id=a)
    in_b = individual(last="Jobperson", organization_id=b)
    await services.providers.add(in_a)
    await services.providers.add(in_b)

    report = await services.sync_source.run(SOURCE)

    assert report.recheck is not None
    assert report.recheck.source_id == SOURCE
    for provider in (in_a, in_b):
        (result,) = await _history(services, provider.organization_id, provider.id)
        assert result.organization_id == provider.organization_id
        # A name-only match is never more than a possible match.
        assert result.finding is Finding.AMBIGUOUS
        assert await _history(services, b if provider is in_a else a, provider.id) == ()
        status = await services.provider_status.run(provider.organization_id, provider.id)
        assert status.attention is Attention.NEEDS_REVIEW
    cursor = await db_connection.execute(
        t"""
        select count(*) from verifications v join providers p on p.id = v.provider_id
        where v.created_at = {clock.now()} and v.organization_id <> p.organization_id
        """
    )
    assert await cursor.fetchone() == (0,)
    summary_a = await services.provider_status_summary.run(a)
    assert (summary_a.total, summary_a.counts[Attention.NEEDS_REVIEW]) == (1, 1)


async def test_completed_only_skips_results_that_could_not_be_checked(tenants: _Tenants) -> None:
    answered = _result(tenants.a_clear, minutes=10)
    unavailable = _result(tenants.a_clear, minutes=20).model_copy(
        update={"outcome": Outcome.SOURCE_UNAVAILABLE, "finding": None}
    )
    await tenants.verifications.add_many([answered, unavailable])

    newest = await tenants.verifications.latest_per_source(
        tenants.a, tenants.a_clear.id, CredentialType.EXCLUSION
    )
    last_answers = await tenants.verifications.latest_per_source(
        tenants.a, tenants.a_clear.id, CredentialType.EXCLUSION, completed_only=True
    )
    last_answer = await tenants.verifications.latest(
        tenants.a, tenants.a_clear.id, CredentialType.EXCLUSION, SOURCE, completed_only=True
    )

    assert [r.id for r in newest] == [unavailable.id]
    assert [r.id for r in last_answers] == [answered.id]
    assert last_answer == answered


async def test_an_import_stores_and_first_checks_its_own_organization_only(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
        content_hash="cd" * 32,
    )
    registry = ConnectorRegistry()
    registry.register(ScriptedConnector(capabilities(SOURCE)))
    services = build_services(database, registry, FixedClock(NOW + timedelta(hours=1)))
    await services.snapshots.add(snapshot)
    listed = exclusion(source_id=SOURCE, snapshot_id=snapshot.id, last="Importperson")
    await services.records.replace_current(
        SOURCE, snapshot.id, (listed,), snapshot.fetched_at, source_changed=True
    )
    a, b = await _organization(db_connection), await _organization(db_connection)
    theirs = individual(last="Importperson", first="Avery", organization_id=b)
    await services.providers.add(theirs)
    text = "Last\tFirst\tRole\nImportperson\tAvery\temployee\nOtherperson\tBlake\t\n"

    report = await services.import_providers.run(a, text, dry_run=False)

    assert [row.existing_provider_id for row in report.rows] == [None, None]
    assert report.recheck is not None
    assert (report.recheck.trigger, report.recheck.organizations) == ("import", 1)
    assert (report.recheck.providers, report.recheck.results, report.recheck.failures) == (
        2,
        2,
        0,
    )
    roster = await services.providers.list_all(a)
    assert [provider.id for provider, _ in roster] == list(report.imported)
    assert roster[0][0].role is RosterRole.WORKFORCE
    for provider, status in roster:
        (result,) = await _history(services, a, provider.id)
        assert result.organization_id == a
        assert status is not None
    importperson = roster[0][1]
    assert importperson is not None
    assert importperson.attention is Attention.NEEDS_REVIEW
    assert await _history(services, b, theirs.id) == ()
    assert await services.providers.list_all(b) == [(theirs, None)]
    cursor = await db_connection.execute(
        t"select trigger, providers from monitoring_runs where id = {report.recheck.id}"
    )
    assert await cursor.fetchone() == ("import", 2)
