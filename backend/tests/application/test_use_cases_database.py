"""The use cases over the real repositories, inside a rolled-back transaction."""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from credentialing.application.source_status import SourceHealth
from credentialing.bootstrap import Services, build_services
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    EntityKind,
    Finding,
    Freshness,
    MatchLevel,
    Outcome,
    Verdict,
)
from credentialing.domain.models import FetchResult, SnapshotDiff, SourceSnapshot
from credentialing.infrastructure.database import ConnectionSource
from tests.fakes import FixedClock, ScriptedConnector, capabilities
from tests.synthetic import NOW, exclusion, individual, make_npi

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_federal_list"
NPI = make_npi("100000009")


def _snapshot(hours: int) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW + timedelta(hours=hours),
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.csv",
        content_hash=f"{hours:02d}" * 32,
    )


def _services(
    database: ConnectionSource, connector: ScriptedConnector, clock: FixedClock
) -> Services:
    registry = ConnectorRegistry()
    registry.register(connector)
    return build_services(database, registry, clock)


async def _organization(connection: AsyncConnection[TupleRow]) -> UUID:
    organization_id = uuid4()
    await connection.execute(
        t"insert into organizations (id, name) values ({organization_id}, 'Testorg Clinic')"
    )
    return organization_id


async def test_two_syncs_add_then_change_rows(database: ConnectionSource) -> None:
    first, second = _snapshot(0), _snapshot(1)
    connector = ScriptedConnector(
        capabilities(SOURCE),
        FetchResult(
            snapshot=first,
            records=(
                exclusion(source_id=SOURCE, snapshot_id=first.id, last="Alphaperson"),
                exclusion(source_id=SOURCE, snapshot_id=first.id, last="Betaperson"),
            ),
        ),
        FetchResult(
            snapshot=second,
            records=(
                exclusion(source_id=SOURCE, snapshot_id=second.id, last="Alphaperson"),
                exclusion(
                    source_id=SOURCE,
                    snapshot_id=second.id,
                    last="Betaperson",
                    end_date=date(2026, 1, 1),
                ),
            ),
        ),
    )
    clock = FixedClock(NOW)
    services = _services(database, connector, clock)

    added = await services.sync_source.run(SOURCE)
    clock.current = NOW + timedelta(hours=1)
    changed = await services.sync_source.run(SOURCE)

    assert added.diff == SnapshotDiff(added=2, removed=0, changed=0, unchanged=0)
    assert changed.diff == SnapshotDiff(added=0, removed=0, changed=1, unchanged=1)
    assert connector.previous_seen == [None, first]
    assert await services.snapshots.latest(SOURCE) == second
    current = await services.records.find_candidates(None, "Betaperson", None)
    assert [record.end_date for record in current if record.source_id == SOURCE] == [
        date(2026, 1, 1)
    ]


async def test_check_exclusions_end_to_end(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    snapshot = _snapshot(0)
    listed = exclusion(source_id=SOURCE, snapshot_id=snapshot.id, last="Gammaperson", npi=NPI)
    connector = ScriptedConnector(
        capabilities(SOURCE), FetchResult(snapshot=snapshot, records=(listed,))
    )
    clock = FixedClock(NOW)
    services = _services(database, connector, clock)
    organization_id = await _organization(db_connection)
    provider = individual(last="Gammaperson", npi=NPI, organization_id=organization_id)
    await services.providers.add(provider)
    await services.sync_source.run(SOURCE)
    clock.current = NOW + timedelta(hours=2)

    verdict = await services.check_exclusions.run(organization_id, provider.id)

    (result,) = verdict.results
    assert verdict.verdict is Verdict.LISTED
    assert await services.provider_verdict.run(organization_id, provider.id) == verdict

    assert result.finding is Finding.RECORD_FOUND
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT
    assert result.provenance.snapshot_id == snapshot.id
    (stored,) = await services.verifications.history_all(organization_id, provider.id)
    assert stored == result
    (view,) = await services.verification_history.expand([result])
    assert [record.npi for record in view.matched_records] == [NPI]


async def test_source_status_over_the_real_repositories(database: ConnectionSource) -> None:
    loaded = _snapshot(0)
    failed = SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW + timedelta(hours=2),
        outcome=Outcome.SOURCE_UNAVAILABLE,
        source_url="https://example.test/list.csv",
        note="synthetic timeout",
    )
    connector = ScriptedConnector(
        capabilities(SOURCE),
        FetchResult(
            snapshot=loaded,
            records=(
                exclusion(source_id=SOURCE, snapshot_id=loaded.id, last="Alphaperson"),
                exclusion(source_id=SOURCE, snapshot_id=loaded.id, last="Betaperson"),
            ),
        ),
        FetchResult(snapshot=failed),
    )
    clock = FixedClock(NOW)
    services = _services(database, connector, clock)

    (never,) = await services.source_status.run()
    await services.sync_source.run(SOURCE)
    clock.current = NOW + timedelta(hours=1)
    (ok,) = await services.source_status.run()
    clock.current = NOW + timedelta(hours=2)
    await services.sync_source.run(SOURCE)
    clock.current = NOW + timedelta(hours=3)
    (failing,) = await services.source_status.run()

    assert never.health is SourceHealth.NEVER_FETCHED
    assert never.current_records == 0
    assert ok.health is SourceHealth.OK
    assert (ok.current_records, ok.latest, ok.freshness) == (2, loaded, Freshness.CACHED)
    assert ok.next_due == NOW + timedelta(days=7)
    assert failing.health is SourceHealth.FAILING
    assert (failing.latest, failing.latest_completed) == (failed, loaded)
    assert failing.problem == "synthetic timeout"
    assert failing.current_records == 2


async def test_a_related_npi_without_a_matching_name_needs_review_and_shows_on_the_profile(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    snapshot = _snapshot(0)
    owner_npi = make_npi("100000008")
    business = exclusion(
        source_id=SOURCE,
        snapshot_id=snapshot.id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Deltasample Dentures NW",
        npi=NPI,
        related_npis=(owner_npi,),
        related_parties=("Deltaowner, Jordan L. DN",),
    )
    connector = ScriptedConnector(
        capabilities(SOURCE), FetchResult(snapshot=snapshot, records=(business,))
    )
    clock = FixedClock(NOW)
    services = _services(database, connector, clock)
    organization_id = await _organization(db_connection)
    provider = individual(last="Unnamedperson", npi=owner_npi, organization_id=organization_id)
    await services.providers.add(provider)
    await services.sync_source.run(SOURCE)

    verdict = await services.check_exclusions.run(organization_id, provider.id)
    profile = await services.record_profile.run(business.id)

    assert verdict.verdict is Verdict.NEEDS_REVIEW
    (result,) = verdict.results
    assert result.match is not None
    assert result.match.level is MatchLevel.POTENTIAL
    assert profile.source.fetched_at == snapshot.fetched_at
    assert profile.record.record.related_npis == (owner_npi,)
    assert profile.record.record.related_parties == ("Deltaowner, Jordan L. DN",)
