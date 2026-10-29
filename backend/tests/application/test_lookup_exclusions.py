from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import EntityKind, Outcome
from credentialing.domain.models import Address, ExclusionRecord, License, SourceSnapshot
from tests.fakes import InMemoryRecords, InMemorySnapshots, ScriptedConnector, capabilities
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = pytest.mark.anyio

FEDERAL = "fake_federal"
STATE = "fake_ny_list"
OTHER_STATE = "fake_wa_list"
NPI = make_npi("100000001")
LIST_UPDATED = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)


class _World:
    def __init__(self, *, with_washington: bool = False) -> None:
        registry = ConnectorRegistry()
        registry.register(ScriptedConnector(capabilities(FEDERAL, display_name="B Federal List")))
        registry.register(
            ScriptedConnector(
                capabilities(STATE, frozenset({"NY"}), display_name="A New York List").model_copy(
                    update={"provider_type_fields": ("PROVIDER_TYPE",)}
                )
            )
        )
        if with_washington:
            registry.register(
                ScriptedConnector(
                    capabilities(OTHER_STATE, frozenset({"WA"}), display_name="C Washington List")
                )
            )
        self.records = InMemoryRecords()
        self.snapshots = InMemorySnapshots()
        self.use_case = LookupExclusions(self.records, self.snapshots, registry)

    async def load(self, source_id: str, *records: ExclusionRecord) -> SourceSnapshot:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=source_id,
            fetched_at=NOW - timedelta(hours=3),
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/list",
            last_modified=LIST_UPDATED,
        )
        await self.snapshots.add(snapshot)
        own = tuple(r.model_copy(update={"snapshot_id": snapshot.id}) for r in records)
        await self.records.replace_current(source_id, snapshot.id, own, snapshot.fetched_at)
        return snapshot


def _pharmacy() -> ExclusionRecord:
    return exclusion(
        source_id=STATE,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="7 STAR SAMPLEVILLE PHARMACY AND DELI INC",
        npi=NPI,
        license=License(number="00012345", state="NY"),
        raw={
            "PROVIDER_NAME": "7 STAR SAMPLEVILLE PHARMACY AND DELI INC",
            "PROVIDER_TYPE": "Pharmacy",
        },
    )


@pytest.mark.parametrize("query", [NPI, "STAR SAMPLEVILLE PHARMACY", "  star pharm ", "00012345"])
async def test_finds_a_record_by_npi_name_words_or_licence(query: str) -> None:
    world = _World()
    pharmacy = _pharmacy()
    await world.load(STATE, pharmacy, exclusion(source_id=STATE, last="Otherperson"))

    result = await world.use_case.run(query, limit=20)

    assert result.query == query.strip()
    assert [found.record.organization_name for found in result.records] == [
        pharmacy.organization_name
    ]
    assert result.records[0].provider_type == "Pharmacy"


async def test_reports_every_list_with_its_dates_even_without_matches() -> None:
    world = _World()
    state = await world.load(STATE)

    result = await world.use_case.run("Nobodyperson", limit=20)

    assert result.records == ()
    assert [(s.source_id, s.display_name, s.jurisdictions) for s in result.sources] == [
        (STATE, "A New York List", ("NY",)),
        (FEDERAL, "B Federal List", ("US",)),
    ]
    loaded, never = result.sources
    assert (loaded.fetched_at, loaded.list_last_updated) == (state.fetched_at, LIST_UPDATED)
    assert (never.fetched_at, never.list_last_updated) == (None, None)


async def test_a_source_without_provider_type_columns_gives_none() -> None:
    world = _World()
    await world.load(FEDERAL, exclusion(source_id=FEDERAL, raw={"SPECIALTY": "NURSE"}))

    (found,) = (await world.use_case.run("Testperson", limit=20)).records

    assert found.provider_type is None


@pytest.mark.parametrize("query", ["", "   ", "a", " b "])
async def test_short_queries_search_nothing(query: str) -> None:
    world = _World()
    await world.load(FEDERAL, exclusion(source_id=FEDERAL, last="A"))

    result = await world.use_case.run(query, limit=20)

    assert (result.records, result.sources) == ((), ())


async def test_the_limit_is_clamped() -> None:
    world = _World()
    await world.load(FEDERAL, *(exclusion(source_id=FEDERAL, first=f"Avery{i}") for i in range(60)))

    assert len((await world.use_case.run("Testperson", limit=500)).records) == 50
    assert len((await world.use_case.run("Testperson", limit=0)).records) == 1


def _living_in(record: ExclusionRecord, state: str) -> ExclusionRecord:
    return record.model_copy(update={"address": Address(city="Sampleville", state=state)})


async def test_a_state_keeps_its_own_lists_and_records_tied_to_it() -> None:
    world = _World(with_washington=True)
    on_the_list = exclusion(source_id=STATE, first="Onlist")
    licensed_there = exclusion(
        source_id=OTHER_STATE, first="Licensed", license=License(number="QZ1", state="NY")
    )
    federal_there = _living_in(exclusion(source_id=FEDERAL, first="Federal"), "NY")
    federal_elsewhere = _living_in(exclusion(source_id=FEDERAL, first="Elsewhere"), "WA")
    other_list = _living_in(exclusion(source_id=OTHER_STATE, first="Otherlist"), "WA")
    await world.load(STATE, on_the_list)
    await world.load(OTHER_STATE, licensed_there, other_list)
    await world.load(FEDERAL, federal_there, federal_elsewhere)

    result = await world.use_case.run("Testperson", limit=50, state="NY")

    assert result.state == "NY"
    assert {found.record.id for found in result.records} == {
        on_the_list.id,
        licensed_there.id,
        federal_there.id,
    }
    assert [s.source_id for s in result.sources] == [STATE, FEDERAL, OTHER_STATE]


async def test_without_a_state_every_record_is_kept() -> None:
    world = _World(with_washington=True)
    await world.load(STATE, exclusion(source_id=STATE))
    await world.load(OTHER_STATE, _living_in(exclusion(source_id=OTHER_STATE), "WA"))

    result = await world.use_case.run("Testperson", limit=50)

    assert result.state is None
    assert len(result.records) == 2


async def test_a_state_without_lists_still_finds_records_living_there() -> None:
    world = _World()
    living = _living_in(exclusion(source_id=FEDERAL), "TX")
    await world.load(FEDERAL, living, exclusion(source_id=FEDERAL, first="Nowhere"))

    result = await world.use_case.run("Testperson", limit=50, state="TX")

    assert [found.record.id for found in result.records] == [living.id]


async def test_a_short_query_keeps_the_state() -> None:
    result = await _World().use_case.run("a", limit=20, state="NY")

    assert (result.state, result.records) == ("NY", ())
