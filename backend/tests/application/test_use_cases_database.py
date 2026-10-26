"""The use cases over the real repositories, inside a rolled-back transaction."""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from credentialing.bootstrap import Services, build_services
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import Finding, MatchLevel, Outcome
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

    (result,) = await services.check_exclusions.run(organization_id, provider.id)

    assert result.finding is Finding.RECORD_FOUND
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT
    assert result.provenance.snapshot_id == snapshot.id
    (stored,) = await services.verifications.history_all(organization_id, provider.id)
    assert stored == result
    (view,) = await services.verification_history.expand([result])
    assert [record.npi for record in view.matched_records] == [NPI]
