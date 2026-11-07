"""The NPI links, the mirror's search keys and its search, over the local database.

Every test starts from empty links, runs, mirror and exclusion tables, truncated inside the
test's rolled-back transaction (see the ``world`` fixture): the development database holds the
real registry and the real lists, which must neither be scanned nor disturbed.
"""

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from credentialing.application.link_npis import LinkNpis
from credentialing.connectors.nppes.reader import NppesCsvDataset
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    DatasetLoadMode,
    EntityKind,
    MatchField,
    MatchLevel,
    Outcome,
)
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    MatchEvidence,
    MatchResult,
    NpiLink,
    NpiLinkRun,
    NpiRecord,
    SourceSnapshot,
)
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.policies import CANDIDATE_CAP
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.exclusion_records import (
    PostgresExclusionRecordRepository,
)
from credentialing.infrastructure.repositories.npi_links import PostgresNpiLinkRepository
from credentialing.infrastructure.repositories.npi_registry import PostgresNpiRegistryRepository
from credentialing.infrastructure.repositories.snapshots import PostgresSnapshotRepository
from tests.connectors.fixtures import nppes_builders as b
from tests.fakes import FixedClock, ListDataset, ScriptedDatasetConnector, dataset_capabilities
from tests.synthetic import NOW, exclusion, make_npi, npi_organization, npi_person, npi_stub

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_exclusions"
OTHER_SOURCE = "test_other_list"
MIRROR = "test_nppes"
SAMPLEVILLE = Address(city="Sampleville", state="NY", postal_code="99901")


def npi(number: int) -> str:
    return make_npi(f"16{number:07d}")


class _World:
    """The repositories over the emptied tables, with helpers that load the mirror and lists."""

    def __init__(self, database: ConnectionSource) -> None:
        self.database = database
        self.links = PostgresNpiLinkRepository(database)
        self.registry = PostgresNpiRegistryRepository(database)
        self.records = PostgresExclusionRecordRepository(database)
        self.snapshots = PostgresSnapshotRepository(database)

    async def snapshot(self, source_id: str) -> UUID:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=source_id,
            fetched_at=NOW,
            outcome=Outcome.COMPLETED,
            source_url=f"https://example.test/{source_id}",
        )
        await self.snapshots.add(snapshot)
        return snapshot.id

    async def mirror(self, *records: NpiRecord) -> UUID:
        """Load ``records`` as a full file; return its snapshot id."""
        snapshot_id = await self.snapshot(MIRROR)
        load = await self.registry.load(
            ListDataset(*records), mode=DatasetLoadMode.FULL, snapshot_id=snapshot_id, now=NOW
        )
        await self.snapshots.set_load(snapshot_id, load)
        return snapshot_id

    async def listed(self, *records: ExclusionRecord, source_id: str = SOURCE) -> None:
        snapshot_id = await self.snapshot(source_id)
        own = [
            r.model_copy(update={"snapshot_id": snapshot_id, "source_id": source_id})
            for r in records
        ]
        await self.records.replace_current(source_id, snapshot_id, own, NOW, source_changed=True)

    async def found(self, record: ExclusionRecord) -> tuple[list[str], int]:
        (item,) = await self.links.candidates([record.id])
        return [candidate.npi for candidate in item.candidates], item.found


@pytest.fixture
async def world(database: ConnectionSource) -> _World:
    """The repositories over empty links, runs, mirror and lists.

    Truncation is transactional and O(1), so the real rows are back once the test's
    transaction rolls back; without it the real registry and lists would be scanned.
    """
    async with database.connection() as connection:
        await connection.execute(
            "truncate exclusion_npi_links, npi_link_runs, npi_registry, npi_registry_history,"
            " exclusion_records"
        )
    return _World(database)


# --- Keys ---------------------------------------------------------------------------------

KEY_CASES = [
    "O'Brien, Mary-Ann M.D.",
    "SMITH JR",
    "Smith  Jr.  MD",
    "DO",
    "Do Md",
    "md do",
    "VAN DER BERG III",
    "St. John's",
    "Jean-Luc",
    "A.B.",
    "...",
    "The Acme Group, LLC",
    "Acme Co Inc",
    "co acme inc",
    "The",
    "The Co",
    "acme pa",
    "Acme, Inc.",
    "acme incorporated group",
    "José Ñuñez",
    "ZOË ÅSTRÖM",
]


async def test_sql_keys_equal_the_python_normalization(world: _World) -> None:
    async with world.database.connection() as connection:
        cursor = await connection.execute(
            t"""
            select t, name_key(t), organization_key(t) from unnest({KEY_CASES}::text[]) as t
            """
        )
        rows = await cursor.fetchall()

    assert [(text, name or "", organization or "") for text, name, organization in rows] == [
        (text, normalize_name(text), normalize_organization_name(text)) for text in KEY_CASES
    ]


async def test_a_real_load_fills_the_generated_keys(world: _World, tmp_path: Path) -> None:
    snapshot_id = await world.snapshot(MIRROR)
    path = tmp_path / "monthly.zip"
    path.write_bytes(b.nppes_zip([b.person(), b.organization(), b.stub()]))
    dataset = NppesCsvDataset(path, snapshot_id)
    try:
        await world.registry.load(
            dataset, mode=DatasetLoadMode.FULL, snapshot_id=snapshot_id, now=NOW
        )
    finally:
        dataset.close()

    async with world.database.connection() as connection:
        cursor = await connection.execute(
            """
            select npi, last_name_key, first_name_key, organization_key, other_name_keys
            from npi_registry order by npi
            """
        )
        rows = {row[0]: row[1:] for row in await cursor.fetchall()}
    assert rows[b.PERSON_NPI] == ("testperson", "avery", None, ["sampleford avery"])
    assert rows[b.ORGANIZATION_NPI] == (
        None,
        None,
        "sampleville care group",
        [
            "sampleville clinic",
            "sampleville health holdings",
        ],
    )
    assert rows[b.STUB_NPI] == (None, None, None, [])


async def test_the_keys_follow_a_changed_row_through_the_upsert(world: _World) -> None:
    await world.mirror(npi_person(npi(1)), npi_person(npi(2)))
    renamed = npi_person(npi(1), last="O'SAMPLE-SMITH", first="RILEY").model_copy(
        update={"line_hash": "f" * 64}
    )

    load = await world.registry.load(
        ListDataset(renamed, npi_person(npi(2))),
        mode=DatasetLoadMode.FULL,
        snapshot_id=await world.snapshot(MIRROR),
        now=NOW + timedelta(days=30),
    )

    assert load.changed == 1
    async with world.database.connection() as connection:
        cursor = await connection.execute(
            t"select last_name_key, first_name_key from npi_registry where npi = {npi(1)}"
        )
        assert await cursor.fetchone() == ("osample smith", "riley")


# --- Candidates ---------------------------------------------------------------------------


async def test_a_person_is_found_by_last_and_first_name_only(world: _World) -> None:
    await world.mirror(
        npi_person(npi(1)),
        npi_person(npi(2), last="OTHERPERSON"),
        npi_person(npi(3), first="RILEY"),
        npi_organization(npi(4), "TESTPERSON AVERY"),
    )
    record = exclusion(last="Testperson", first="Avery")
    await world.listed(record)

    assert await world.found(record) == ([npi(1)], 1)


async def test_initials_are_found_on_either_side(world: _World) -> None:
    await world.mirror(
        npi_person(npi(1)),
        npi_person(npi(2), first="ALEX"),
        npi_person(npi(3), first="A"),
        npi_person(npi(4), first="RILEY"),
    )
    initial = exclusion(first="A.")
    full = exclusion(first="Avery", middle="Q")
    await world.listed(initial, full)

    assert await world.found(initial) == ([npi(1), npi(2), npi(3)], 3)
    assert await world.found(full) == ([npi(1), npi(3)], 2)


async def test_aliases_are_searched_with_and_without_a_comma(world: _World) -> None:
    await world.mirror(npi_person(npi(1)), npi_person(npi(2), last="SAMPLEFORD", first="RILEY"))
    comma = exclusion(last="Marriedname", aliases=("Testperson, Avery J",))
    words = exclusion(last="Marriedname", first="Jo", aliases=("Riley Q. Sampleford",))
    await world.listed(comma, words)

    assert await world.found(comma) == ([npi(1)], 1)
    assert await world.found(words) == ([npi(2)], 1)


async def test_an_organization_is_found_by_name_other_name_and_alias(world: _World) -> None:
    await world.mirror(
        npi_organization(npi(1)),
        npi_organization(npi(2), "SAMPLE HOLDINGS", other_names=("THE SAMPLEVILLE CLINIC INC",)),
        npi_organization(npi(3), "OTHER CLINIC"),
    )
    by_name = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Sampleville Care Group, Inc.",
    )
    by_other = exclusion(
        entity_kind=EntityKind.ORGANIZATION, last=None, organization_name="Sampleville Clinic"
    )
    by_alias = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Unrelated Name",
        aliases=("Other Clinic LLC",),
    )
    await world.listed(by_name, by_other, by_alias)

    assert await world.found(by_name) == ([npi(1)], 1)
    assert await world.found(by_other) == ([npi(2)], 1)
    assert await world.found(by_alias) == ([npi(3)], 1)


async def test_a_published_npi_finds_its_row_only_listed_or_not(world: _World) -> None:
    await world.mirror(npi_person(npi(1), last="OTHERPERSON"), npi_person(npi(2)))
    await world.mirror(npi_person(npi(2)))  # a full file without npi(1): flagged, not in_file
    record, unknown = exclusion(npi=npi(1)), exclusion(last="Gone", npi=npi(9))
    await world.listed(record, unknown)

    assert await world.found(record) == ([npi(1)], 1)
    assert await world.found(unknown) == ([], 0)


async def test_name_discovery_ignores_npis_the_latest_file_dropped(world: _World) -> None:
    await world.mirror(npi_person(npi(1)), npi_person(npi(2)))
    await world.mirror(npi_person(npi(2)))
    record = exclusion()
    await world.listed(record)

    assert await world.found(record) == ([npi(2)], 1)


async def test_a_person_without_a_first_name_has_no_candidates(world: _World) -> None:
    await world.mirror(npi_person(npi(1)))
    record = exclusion(first="")
    await world.listed(record)

    assert await world.found(record) == ([], 0)


async def test_more_than_the_cap_returns_the_strongest_first_and_says_more(world: _World) -> None:
    namesakes = [npi_person(npi(n)) for n in range(1, CANDIDATE_CAP + 5)]
    nearby = npi_person(
        npi(99), practice=SAMPLEVILLE.model_copy(update={"postal_code": "999010000"})
    )
    await world.mirror(*namesakes, nearby)
    record = exclusion().model_copy(update={"address": SAMPLEVILLE})
    await world.listed(record)

    found, count = await world.found(record)

    assert count == CANDIDATE_CAP + 1
    assert len(found) == CANDIDATE_CAP
    assert found[0] == npi(99)


async def test_one_statement_serves_a_batch_of_records(world: _World) -> None:
    await world.mirror(npi_person(npi(1)), npi_organization(npi(2)))
    person = exclusion()
    business = exclusion(
        entity_kind=EntityKind.ORGANIZATION, last=None, organization_name="Sampleville Care Group"
    )
    nobody = exclusion(last="Nobody")
    await world.listed(person, business, nobody)

    found = await world.links.candidates([person.id, business.id, nobody.id, uuid4()])

    assert {item.record.id: [c.npi for c in item.candidates] for item in found} == {
        person.id: [npi(1)],
        business.id: [npi(2)],
        nobody.id: [],
    }


# --- Links, staleness, summary, runs --------------------------------------------------------


def _link(
    record: ExclusionRecord,
    number: str | None,
    level: MatchLevel,
    mirror_snapshot_id: UUID,
) -> NpiLink:
    evidence = (MatchEvidence(field=MatchField.LAST_NAME, matched=True),) if number else ()
    return NpiLink(
        id=uuid4(),
        record_id=record.id,
        npi=number,
        match=MatchResult(level=level, evidence=evidence, explanation=f"{level.value}."),
        candidates=2,
        mirror_snapshot_id=mirror_snapshot_id,
        linked_at=NOW,
    )


async def test_links_are_rewritten_whole_and_read_best_first(world: _World) -> None:
    mirror = await world.mirror(npi_person(npi(1)), npi_person(npi(2)), npi_stub(npi(3)))
    record, other = exclusion(), exclusion(last="Otherperson")
    await world.listed(record, other)
    await world.links.replace_links(
        [
            _link(record, npi(1), MatchLevel.AMBIGUOUS, mirror),
            _link(record, npi(3), MatchLevel.AMBIGUOUS, mirror),
            _link(other, None, MatchLevel.NONE, mirror),
        ]
    )

    await world.links.replace_links(
        [
            _link(record, npi(2), MatchLevel.AMBIGUOUS, mirror),
            _link(record, npi(1), MatchLevel.HIGH, mirror),
        ]
    )

    links = await world.links.links_for([record.id, other.id, uuid4()])
    assert [(link.npi, link.level) for link in links[record.id]] == [
        (npi(1), MatchLevel.HIGH),
        (npi(2), MatchLevel.AMBIGUOUS),
    ]
    assert links[record.id][0].match.evidence == (
        MatchEvidence(field=MatchField.LAST_NAME, matched=True),
    )
    ((none,),) = [links[other.id]]
    assert (none.npi, none.level, none.match.explanation) == (None, MatchLevel.NONE, "none.")
    assert set(links) == {record.id, other.id}


async def test_stale_records_are_the_unlinked_and_those_linked_to_another_load(
    world: _World,
) -> None:
    old = await world.mirror(npi_person(npi(1)))
    new = await world.mirror(npi_person(npi(1)), npi_person(npi(2)))
    linked, outdated, never = exclusion(), exclusion(last="B"), exclusion(last="C")
    elsewhere = exclusion(last="D")
    await world.listed(linked, outdated, never)
    await world.listed(elsewhere, source_id=OTHER_SOURCE)
    await world.links.replace_links(
        [
            _link(linked, npi(1), MatchLevel.POTENTIAL, new),
            _link(outdated, npi(1), MatchLevel.POTENTIAL, old),
        ]
    )

    every = await world.links.stale_records(source_id=None, mirror_snapshot_id=new)
    own = await world.links.stale_records(source_id=SOURCE, mirror_snapshot_id=new)
    one = await world.links.stale_records(source_id=None, mirror_snapshot_id=new, limit=1)

    assert set(every) == {outdated.id, never.id, elsewhere.id}
    assert set(own) == {outdated.id, never.id}
    assert list(every) == sorted(every)
    assert one == every[:1]


async def test_the_summary_counts_records_by_their_best_link(world: _World) -> None:
    mirror = await world.mirror(npi_person(npi(1)), npi_person(npi(2)))
    high, none, never = exclusion(), exclusion(last="B"), exclusion(last="C")
    await world.listed(high, none, never)
    await world.links.replace_links(
        [
            _link(high, npi(1), MatchLevel.AMBIGUOUS, mirror),
            _link(high, npi(2), MatchLevel.HIGH, mirror),
            _link(none, None, MatchLevel.NONE, mirror),
        ]
    )

    summary = await world.links.summary()

    assert summary.records == 3
    assert dict(summary.by_best_level) == {MatchLevel.HIGH: 1, MatchLevel.NONE: 1}
    assert summary.never_checked == 1
    assert summary.last_run is None


async def test_runs_round_trip_with_phases_in_order(world: _World) -> None:
    mirror = await world.mirror(npi_person(npi(1)))
    older = NpiLinkRun(
        id=uuid4(),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=5),
        trigger="cli",
        records=0,
        linked=0,
        note="the NPI mirror has never been loaded; nothing was linked",
    )
    run = NpiLinkRun(
        id=uuid4(),
        started_at=NOW + timedelta(minutes=1),
        finished_at=NOW + timedelta(minutes=4),
        trigger="list_sync:test_exclusions",
        mirror_snapshot_id=mirror,
        records=10,
        linked=7,
        by_level={level: index for index, level in enumerate(MatchLevel)},
        phases={
            "writing": timedelta(seconds=3),
            "candidates": timedelta(seconds=60.5),
            "scoring": timedelta(seconds=1),
        },
        cpu_time=timedelta(seconds=42.25),
    )
    await world.links.add_run(older)
    await world.links.add_run(run)

    stored = await world.links.last_run()

    assert stored == run
    assert stored is not None
    assert list(stored.phases) == ["writing", "candidates", "scoring"]


async def test_linking_end_to_end_over_the_database(world: _World) -> None:
    registry = ConnectorRegistry()
    registry.register(ScriptedDatasetConnector(dataset_capabilities(MIRROR)))
    near = SAMPLEVILLE.model_copy(update={"postal_code": "999011234"})
    await world.mirror(npi_person(npi(1), practice=near), npi_person(npi(2)), npi_stub(npi(3)))
    person = exclusion().model_copy(update={"address": SAMPLEVILLE})
    published = exclusion(last="Gone", npi=npi(3))
    nobody = exclusion(last="Nobody")
    await world.listed(person, published, nobody)
    clock = FixedClock(NOW)
    use_case = LinkNpis(registry, world.snapshots, world.links, clock, batch_size=2)

    run = await use_case.run(source_id=SOURCE, trigger="cli")
    clock.current = NOW + timedelta(minutes=1)
    again = await use_case.run(source_id=SOURCE, trigger="cli")

    assert (run.records, run.linked) == (3, 2)
    assert run.by_level[MatchLevel.HIGH] == 2
    assert run.by_level[MatchLevel.NONE] == 1
    assert list(run.phases) == ["candidates", "scoring", "writing"]
    assert again.records == 0
    links = await world.links.links_for([person.id, published.id, nobody.id])
    assert [(link.npi, link.level) for link in links[person.id]] == [
        (npi(1), MatchLevel.HIGH),
        (npi(2), MatchLevel.AMBIGUOUS),
    ]
    assert [link.level for link in links[published.id]] == [MatchLevel.HIGH]
    assert links[nobody.id][0].match.explanation == "No match: no candidate with this name."
    stored = await world.records.get(person.id)
    assert stored is not None
    assert stored.npi is None
    summary = await world.links.summary()
    assert summary.never_checked == 0
    assert summary.last_run == again


# --- Mirror search ------------------------------------------------------------------------


async def test_search_by_npi_name_and_organization(world: _World) -> None:
    await world.mirror(
        npi_person(npi(1)),
        npi_person(npi(2), first="RILEY"),
        npi_person(npi(3), last="AVERY", first="TESTPERSON"),
        npi_organization(npi(4)),
        npi_organization(npi(5), "SAMPLE HOLDINGS", other_names=("SAMPLEVILLE CARE GROUP",)),
    )

    async def search(query: str, limit: int = 20) -> list[str]:
        return [record.npi for record in await world.registry.search(query, limit=limit)]

    assert await search(npi(2)) == [npi(2)]
    assert await search("Testperson Avery") == [npi(3), npi(1)]
    assert await search("avery testperson") == [npi(3), npi(1)]
    assert await search("Testperson") == [npi(1), npi(2)]
    assert await search("Testperson", limit=1) == [npi(1)]
    assert await search("The Sampleville Care Group, Inc.") == [npi(5), npi(4)]
    assert await search("Testp") == []
    assert await search("Nobody Here") == []


async def test_search_and_get_many_see_only_what_they_should(world: _World) -> None:
    await world.mirror(npi_person(npi(1)), npi_person(npi(2)))
    await world.mirror(npi_person(npi(2)))

    found = await world.registry.search("Testperson Avery", limit=20)
    many = await world.registry.get_many([npi(1), npi(2), npi(9)])

    assert [record.npi for record in found] == [npi(2)]
    assert sorted(record.npi for record in many) == [npi(1), npi(2)]
    assert await world.registry.get_many([]) == []
