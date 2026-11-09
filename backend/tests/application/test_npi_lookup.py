"""Reading the NPI mirror and, when needed, the live registry, over in-memory storage."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.application.npi_lookup import NpiLookup
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    DatasetLoadMode,
    Freshness,
    NpiStatus,
    Outcome,
    RegistryStatus,
    VerificationMethod,
)
from credentialing.domain.models import DatasetLoad, NpiAnswer, NpiRecord, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from credentialing.domain.ports import NpiQuery
from tests.fakes import (
    API_SOURCE,
    FixedClock,
    InMemoryNpiRegistry,
    InMemorySnapshots,
    ScriptedDatasetConnector,
    ScriptedNpiRegistryConnector,
    api_failure,
    api_found,
    api_no_result,
    dataset_capabilities,
)
from tests.synthetic import NOW, make_npi, npi_person

pytestmark = pytest.mark.anyio

MONTHLY = "nppes_monthly"
NPI = make_npi("140000011")
OTHER = make_npi("140000012")
NEW = make_npi("140000013")


class _World:
    """A mirror of two NPIs, its monthly load, and a scripted live registry."""

    def __init__(self, *answers: NpiAnswer, days: int = 0, live_registry: bool = True) -> None:
        self.snapshots = InMemorySnapshots()
        self.directory = ConnectorRegistry()
        self.directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.connector = ScriptedNpiRegistryConnector(*answers)
        if live_registry:
            self.directory.register(self.connector)
        self.registry = InMemoryNpiRegistry(
            npi_person(NPI).model_copy(update={"last_updated": date(2026, 1, 2)}),
            npi_person(OTHER, first="RILEY"),
        )
        self.clock = FixedClock(NOW + timedelta(days=days))
        self.lookup = NpiLookup(
            self.registry, self.directory, self.snapshots, DEFAULT_FRESHNESS, self.clock
        )

    async def load_mirror(self) -> SourceSnapshot:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=MONTHLY,
            fetched_at=NOW,
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/nppes/monthly.zip",
            last_modified=NOW - timedelta(days=2),
            load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=2, added=2),
        )
        await self.snapshots.add(snapshot)
        return snapshot

    def api_snapshots(self) -> list[SourceSnapshot]:
        return [s for s in self.snapshots.stored if s.source_id == API_SOURCE]


def _newer(npi: str, **update: object) -> NpiRecord:
    """The registry's answer for ``npi``, updated after the mirror's row."""
    return npi_person(npi).model_copy(
        update={"last_updated": date(2026, 1, 12), "line_hash": "api:" + "e" * 64, **update}
    )


async def test_an_npi_comes_with_the_load_the_mirror_is_as_of() -> None:
    world = _World(days=3)
    loaded = await world.load_mirror()

    view = await world.lookup.get(NPI)

    assert view.record is not None
    assert view.record.npi == NPI
    assert view.mirror is not None
    provenance = view.mirror.provenance
    assert (provenance.source_id, provenance.source_url) == (MONTHLY, loaded.source_url)
    assert provenance.method is VerificationMethod.BULK_DATASET
    assert provenance.verified_at == NOW
    assert provenance.source_updated_at == NOW - timedelta(days=2)
    assert provenance.snapshot_id == loaded.id
    assert view.mirror.freshness is Freshness.CACHED
    assert view.registry_status is RegistryStatus.ACTIVE


async def test_a_fresh_mirror_sends_no_request() -> None:
    world = _World(api_no_result(fetched_at=NOW), days=3)
    await world.load_mirror()

    view = await world.lookup.get(NPI)

    assert view.live is None
    assert world.connector.lookups == []
    assert world.api_snapshots() == []


async def test_an_old_mirror_reads_stale_and_asks_the_live_registry() -> None:
    world = _World(api_found(_newer(NPI), fetched_at=NOW + timedelta(days=40)), days=40)
    await world.load_mirror()

    view = await world.lookup.get(NPI)

    assert view.mirror is not None
    assert view.mirror.freshness is Freshness.STALE
    assert world.connector.lookups == [NPI]
    assert view.live is not None
    assert view.live.freshness is Freshness.LIVE


async def test_a_live_answer_is_stored_and_its_load_kept_on_the_snapshot() -> None:
    answer = api_found(_newer(NPI), fetched_at=NOW + timedelta(days=1))
    world = _World(answer, days=1)
    await world.load_mirror()

    view = await world.lookup.get(NPI, live=True)

    assert view.registry_status is RegistryStatus.ACTIVE
    assert view.record is not None
    assert view.record.last_updated == date(2026, 1, 12)
    assert world.registry.rows[NPI].line_hash.startswith("api:")
    assert world.registry.loads == [(DatasetLoadMode.INCREMENTAL, answer.snapshot.id, None)]
    (stored,) = world.api_snapshots()
    assert stored.load is not None
    assert next(iter(stored.load.phases)) == "request"
    assert stored.load.cpu_time is not None
    live = view.live
    assert live is not None
    assert (live.outcome, live.found) == (Outcome.COMPLETED, True)
    assert live.provenance.source_id == API_SOURCE
    assert live.provenance.method is VerificationMethod.OFFICIAL_API
    assert live.provenance.raw_record_ref == f"source_snapshots/{answer.snapshot.id}"
    assert live.freshness is Freshness.LIVE
    assert view.mirror is not None
    assert view.mirror.freshness is Freshness.CACHED


async def test_an_answer_no_newer_than_the_mirror_writes_nothing() -> None:
    same = npi_person(NPI).model_copy(
        update={"last_updated": date(2026, 1, 2), "line_hash": "api:" + "f" * 64}
    )
    world = _World(api_found(same, fetched_at=NOW), days=1)
    await world.load_mirror()

    view = await world.lookup.get(NPI, live=True)

    assert world.registry.loads == []
    assert world.api_snapshots()[0].load is None
    assert view.registry_status is RegistryStatus.ACTIVE


async def test_the_mirror_deactivation_history_is_carried_into_a_newer_answer() -> None:
    world = _World(api_found(_newer(NPI), fetched_at=NOW), days=1)
    world.registry.rows[NPI] = world.registry.rows[NPI].model_copy(
        update={"deactivation_date": date(2020, 3, 1), "reactivation_date": date(2021, 4, 1)}
    )
    await world.load_mirror()

    view = await world.lookup.get(NPI, live=True)

    assert view.record is not None
    assert (view.record.deactivation_date, view.record.reactivation_date) == (
        date(2020, 3, 1),
        date(2021, 4, 1),
    )
    assert world.registry.rows[NPI].deactivation_date == date(2020, 3, 1)


async def test_an_npi_the_mirror_lacks_is_asked_live_and_learned() -> None:
    world = _World(api_found(_newer(NEW), fetched_at=NOW))
    await world.load_mirror()

    view = await world.lookup.get(NEW)

    assert world.connector.lookups == [NEW]
    assert view.registry_status is RegistryStatus.ACTIVE
    assert NEW in world.registry.rows
    assert view.mirror_record is None


async def test_no_result_keeps_the_mirror_row_and_writes_nothing() -> None:
    world = _World(api_no_result(fetched_at=NOW))
    world.registry.rows[OTHER] = world.registry.rows[OTHER].model_copy(
        update={"status": NpiStatus.DEACTIVATED, "deactivation_date": date(2025, 5, 5)}
    )
    await world.load_mirror()

    active = await world.lookup.get(NPI, live=True)
    deactivated = await world.lookup.get(OTHER, live=True)
    unknown = await world.lookup.get(NEW)

    assert world.registry.loads == []
    assert active.record == world.registry.rows[NPI]
    assert active.live is not None
    assert active.live.found is False
    assert active.registry_status is RegistryStatus.MIRROR_ONLY_ACTIVE
    assert deactivated.registry_status is RegistryStatus.DEACTIVATED
    assert unknown.record is None
    assert unknown.registry_status is RegistryStatus.NOT_FOUND


@pytest.mark.parametrize("outcome", [Outcome.SOURCE_UNAVAILABLE, Outcome.BLOCKED, Outcome.FAILED])
async def test_a_failed_request_keeps_the_mirror_and_never_reads_not_found(
    outcome: Outcome,
) -> None:
    world = _World(api_failure(outcome, fetched_at=NOW))
    await world.load_mirror()

    mirrored = await world.lookup.get(NPI, live=True)
    missing = await world.lookup.get(NEW)

    assert mirrored.record == world.registry.rows[NPI]
    assert mirrored.live is not None
    assert (mirrored.live.outcome, mirrored.live.found) == (outcome, None)
    assert mirrored.live.note == "HTTP 503"
    assert mirrored.registry_status is RegistryStatus.ACTIVE
    assert missing.registry_status is RegistryStatus.UNKNOWN
    assert world.registry.loads == []
    assert len(world.api_snapshots()) == 2


async def test_without_a_live_registry_a_missing_npi_is_unknown() -> None:
    world = _World(live_registry=False)
    await world.load_mirror()

    view = await world.lookup.get(NEW)

    assert (view.record, view.live, view.registry_status) == (None, None, RegistryStatus.UNKNOWN)


async def test_a_row_without_a_mirror_load_is_asked_live() -> None:
    world = _World(api_failure(Outcome.FAILED, fetched_at=NOW))

    view = await world.lookup.get(NPI)

    assert world.connector.lookups == [NPI]
    assert view.registry_status is RegistryStatus.UNKNOWN
    assert view.record == world.registry.rows[NPI]


async def test_a_mirror_search_carries_the_mirror_and_needs_two_characters() -> None:
    world = _World()
    await world.load_mirror()

    found = await world.lookup.search("  Testperson Riley ", limit=10)
    short = await world.lookup.search("T", limit=10)

    assert found.query_text == "Testperson Riley"
    assert found.query is None
    assert found.live is None
    assert [record.npi for record in found.records] == [OTHER]
    assert found.mirror is not None
    assert short.records == ()
    assert world.connector.searches == []


async def test_a_mirror_never_loaded_has_no_provenance_to_give() -> None:
    result = await _World().lookup.search("Testperson", limit=10)

    assert result.mirror is None
    assert len(result.records) == 2


async def test_a_live_search_stores_its_newer_results_in_one_load() -> None:
    answer = api_found(_newer(NEW), _newer(NPI), npi_person(OTHER), fetched_at=NOW)
    world = _World(answer)
    await world.load_mirror()
    query = NpiQuery(last_name="Testperson", state="NY")

    result = await world.lookup.search(limit=500, live=True, query=query)

    assert world.connector.searches == [(query, 200)]
    assert [record.npi for record in result.records] == [NEW, NPI, OTHER]
    assert world.registry.loaded_npis == [[NEW, NPI]]
    assert result.query == query
    assert result.live is not None
    assert result.live.found is True
    assert result.mirror is not None


async def test_a_failed_live_search_has_no_records_and_says_why() -> None:
    world = _World(api_failure(Outcome.BLOCKED, fetched_at=NOW, note="HTTP 429"))
    await world.load_mirror()

    result = await world.lookup.search(limit=50, live=True, query=NpiQuery(last_name="Testperson"))

    assert result.records == ()
    assert result.live is not None
    assert (result.live.outcome, result.live.note) == (Outcome.BLOCKED, "HTTP 429")
    assert world.registry.loads == []


async def test_a_live_search_needs_a_query() -> None:
    with pytest.raises(ValueError, match="structured query"):
        await _World().lookup.search(limit=10, live=True)
