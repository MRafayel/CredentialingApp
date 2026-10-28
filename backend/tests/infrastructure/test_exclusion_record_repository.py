from collections.abc import Sequence
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.domain.enums import EntityKind, Outcome
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    SnapshotDiff,
    SourceSnapshot,
)
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.exclusion_records import (
    PostgresExclusionRecordRepository,
)
from credentialing.infrastructure.repositories.snapshots import PostgresSnapshotRepository
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SOURCE = "test_exclusions"
LATER = NOW + timedelta(days=7)
NPI = make_npi("100000001")


async def _snapshot(database: ConnectionSource) -> UUID:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=SOURCE,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list.tsv",
    )
    await PostgresSnapshotRepository(database).add(snapshot)
    return snapshot.id


def _ours(records: Sequence[ExclusionRecord]) -> set[UUID]:
    """Ids of this test's records; the shared table may also hold real public-source rows."""
    return {record.id for record in records if record.source_id == SOURCE}


async def test_full_record_round_trips(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    record = exclusion(
        snapshot_id=snapshot_id,
        npi=NPI,
        aliases=("Avery Marriedname",),
        related_npis=(make_npi("100000002"),),
        related_parties=("Sample Family Care",),
        date_of_birth=date(1970, 5, 17),
        license=License(number="0012345", state="NY", kind="Physician"),
    ).model_copy(
        update={
            "state_provider_id": "TEST-0001",
            "address": Address(line1="1 Sample Way", city="Sampleville", state="NY"),
            "action_raw": "EXCLUDED ",
            "end_date": date(2029, 3, 1),
            "reason": "synthetic reason",
        }
    )

    await repository.replace_current(SOURCE, snapshot_id, [record], NOW)

    assert await repository.get_many([record.id]) == [record]


async def test_replace_current_keeps_unchanged_closes_removed_and_adds_new(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    first_snapshot = await _snapshot(database)
    kept = exclusion(snapshot_id=first_snapshot, last="Testperson")
    dropped = exclusion(snapshot_id=first_snapshot, last="Samplename")

    first = await repository.replace_current(SOURCE, first_snapshot, [kept, dropped], NOW)

    second_snapshot = await _snapshot(database)
    kept_again = exclusion(snapshot_id=second_snapshot, last="TESTPERSON", raw=kept.raw)
    new = exclusion(snapshot_id=second_snapshot, last="Newname")
    second = await repository.replace_current(SOURCE, second_snapshot, [kept_again, new], LATER)

    assert first == SnapshotDiff(added=2, removed=0, changed=0, unchanged=0)
    assert second == SnapshotDiff(added=1, removed=1, changed=0, unchanged=1)
    stored = {
        record.id: record
        for record in await repository.get_many([kept.id, dropped.id, kept_again.id, new.id])
    }
    assert set(stored) == {kept.id, dropped.id, new.id}
    assert (stored[kept.id].valid_from, stored[kept.id].valid_to) == (NOW, None)
    assert stored[kept.id].snapshot_id == first_snapshot
    assert (stored[dropped.id].valid_from, stored[dropped.id].valid_to) == (NOW, LATER)
    assert (stored[new.id].valid_from, stored[new.id].valid_to) == (LATER, None)


async def test_replace_current_stores_a_repeated_row_once(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    row = exclusion(snapshot_id=snapshot_id)
    repeat = exclusion(snapshot_id=snapshot_id)

    diff = await repository.replace_current(SOURCE, snapshot_id, [row, repeat], NOW)

    assert diff == SnapshotDiff(added=1, removed=0, changed=0, unchanged=0)
    assert await repository.get_many([row.id, repeat.id]) == [row]


async def test_replace_current_with_an_empty_list_closes_every_row(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    await repository.replace_current(SOURCE, snapshot_id, [exclusion(snapshot_id=snapshot_id)], NOW)

    diff = await repository.replace_current(SOURCE, await _snapshot(database), [], LATER)

    assert diff == SnapshotDiff(added=0, removed=1, changed=0, unchanged=0)


async def test_replace_current_versions_a_row_whose_content_changed(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    first_snapshot = await _snapshot(database)
    listed = exclusion(snapshot_id=first_snapshot)
    await repository.replace_current(SOURCE, first_snapshot, [listed], NOW)

    second_snapshot = await _snapshot(database)
    reinstated = exclusion(snapshot_id=second_snapshot, end_date=date(2026, 1, 10), raw=listed.raw)
    diff = await repository.replace_current(SOURCE, second_snapshot, [reinstated], LATER)

    assert diff == SnapshotDiff(added=0, removed=0, changed=1, unchanged=0)
    versions = {
        record.id: record for record in await repository.get_many([listed.id, reinstated.id])
    }
    old, new = versions[listed.id], versions[reinstated.id]
    assert (old.valid_from, old.valid_to, old.end_date) == (NOW, LATER, None)
    assert (new.valid_from, new.valid_to, new.end_date) == (LATER, None, date(2026, 1, 10))
    assert old.record_hash == new.record_hash
    assert new.snapshot_id == second_snapshot
    assert _ours(await repository.find_candidates(None, "Testperson", None)) == {new.id}


async def test_replace_current_rejects_records_of_another_source(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    foreign = exclusion(source_id="test_other_list", snapshot_id=snapshot_id)

    with pytest.raises(ValueError, match="does not belong"):
        await repository.replace_current(SOURCE, snapshot_id, [foreign], NOW)


async def test_find_candidates_by_npi_or_normalized_name(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    by_npi = exclusion(snapshot_id=snapshot_id, last="Othername", npi=NPI)
    by_last_name = exclusion(snapshot_id=snapshot_id, last="O'Testperson")
    by_alias = exclusion(snapshot_id=snapshot_id, last="Marriedname", aliases=("Otestperson A",))
    by_organization = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Sampleville Care Group, LLC",
    )
    unrelated = exclusion(snapshot_id=snapshot_id, last="Unrelated")
    records: list[ExclusionRecord] = [by_npi, by_last_name, by_alias, by_organization, unrelated]
    await repository.replace_current(SOURCE, snapshot_id, records, NOW)

    found_by_npi = await repository.find_candidates(NPI, None, None)
    found_by_name = await repository.find_candidates(None, "OTESTPERSON", None)
    found_by_organization = await repository.find_candidates(
        None, None, "sampleville care group llc"
    )
    found_by_all = await repository.find_candidates(NPI, "otestperson", "Nobody Inc")

    assert _ours(found_by_npi) == {by_npi.id}
    assert _ours(found_by_name) == {by_last_name.id, by_alias.id}
    assert _ours(found_by_organization) == {by_organization.id}
    assert _ours(found_by_all) == {by_npi.id, by_last_name.id, by_alias.id}
    assert await repository.find_candidates(None, None, None) == []


async def test_find_candidates_by_organization_name_without_legal_suffix(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    listed = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="7 STAR QZXSAMPLEVILLE PHARMACY AND DELI INC",
    )
    await repository.replace_current(SOURCE, snapshot_id, [listed], NOW)

    found = await repository.find_candidates(
        None, None, "The 7 Star Qzxsampleville Pharmacy and Deli, LLC"
    )

    assert _ours(found) == {listed.id}


async def test_find_candidates_by_licence_number(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    listed = exclusion(
        snapshot_id=snapshot_id,
        last="Qzxothername",
        license=License(number="qzx 0012345", state="NY"),
    )
    await repository.replace_current(SOURCE, snapshot_id, [listed], NOW)

    found = await repository.find_candidates(None, None, None, license_numbers=["QZX0012345", "  "])

    assert _ours(found) == {listed.id}
    assert await repository.find_candidates(None, None, None, license_numbers=[" "]) == []


async def test_replace_current_refreshes_outdated_name_keys(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    listed = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Qzxtestco LLC",
    )
    await repository.replace_current(SOURCE, snapshot_id, [listed], NOW)
    async with database.connection() as connection:
        old_keys = ["qzxtestco llc"]
        await connection.execute(
            t"update exclusion_records set name_keys = {old_keys} where id = {listed.id}"
        )

    diff = await repository.replace_current(SOURCE, snapshot_id, [listed], LATER)

    assert diff == SnapshotDiff(added=0, removed=0, changed=0, unchanged=1)
    async with database.connection() as connection:
        cursor = await connection.execute(
            t"select name_keys, valid_from from exclusion_records where id = {listed.id}"
        )
        row = await cursor.fetchone()
    assert row is not None
    assert (row[0], row[1]) == (["qzxtestco"], NOW)


async def _search_ids(repository: PostgresExclusionRecordRepository, query: str) -> list[UUID]:
    """This test's records a search returns, in the order returned."""
    return [r.id for r in await repository.search(query, limit=50) if r.source_id == SOURCE]


async def test_search_by_npi_words_prefixes_and_identifiers(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    pharmacy = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="7 STAR QZXSAMPLEVILLE PHARMACY AND DELI INC",
        npi=NPI,
        license=License(number="QZX0012345", state="NY"),
    )
    person = exclusion(
        snapshot_id=snapshot_id,
        last="Qzx-Testperson",
        first="Avery",
        aliases=("Avery Qzxmarriedname",),
    ).model_copy(update={"state_provider_id": "QZXID77"})
    closed = exclusion(snapshot_id=snapshot_id, last="Qzxgoneperson")
    await repository.replace_current(SOURCE, snapshot_id, [pharmacy, person, closed], NOW)
    await repository.replace_current(SOURCE, snapshot_id, [pharmacy, person], LATER)

    assert await _search_ids(repository, NPI) == [pharmacy.id]
    assert await _search_ids(repository, NPI[:6]) == [pharmacy.id]
    assert await _search_ids(repository, "STAR QZXSAMPLEVILLE PHARMACY AND DELI") == [pharmacy.id]
    assert await _search_ids(repository, "  qzxsamplev pharm ") == [pharmacy.id]
    assert await _search_ids(repository, "qzx0012345") == [pharmacy.id]
    assert await _search_ids(repository, "qzx-testperson, avery") == [person.id]
    assert await _search_ids(repository, "qzxmarried") == [person.id]
    assert await _search_ids(repository, "QZXID77") == [person.id]
    assert await _search_ids(repository, "qzxgoneperson") == []
    assert await _search_ids(repository, "qzxsampleville nobody") == []
    assert await repository.search("  ':*&|!  ", limit=10) == []


async def test_search_puts_an_exact_npi_first(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    by_state_id = exclusion(snapshot_id=snapshot_id, last="Aaqzxname").model_copy(
        update={"state_provider_id": NPI}
    )
    by_npi = exclusion(snapshot_id=snapshot_id, last="Zzqzxname", npi=NPI)
    await repository.replace_current(SOURCE, snapshot_id, [by_state_id, by_npi], NOW)

    assert await _search_ids(repository, NPI) == [by_npi.id, by_state_id.id]


async def test_search_ranks_whole_words_in_names_first(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    prefix_only = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Aaa Qzxstopping Center",
        npi=NPI,
    )
    whole_word = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="1 Qzxstop Pharmacy",
    )
    await repository.replace_current(SOURCE, snapshot_id, [prefix_only, whole_word], NOW)

    assert await _search_ids(repository, "1 qzxstop") == [whole_word.id, prefix_only.id]


async def test_search_limit_is_capped(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    rows = [exclusion(snapshot_id=snapshot_id, last="Qzxmany", first=f"A{i}") for i in range(55)]
    await repository.replace_current(SOURCE, snapshot_id, rows, NOW)

    assert len(await _search_ids(repository, "qzxmany")) == 50
    assert len(await repository.search("qzxmany", limit=3)) == 3


async def test_find_candidates_ignores_rows_no_longer_listed(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    await repository.replace_current(SOURCE, snapshot_id, [exclusion(snapshot_id=snapshot_id)], NOW)
    await repository.replace_current(SOURCE, await _snapshot(database), [], LATER)

    assert _ours(await repository.find_candidates(None, "Testperson", None)) == set()


async def test_get_many_with_no_ids(database: ConnectionSource) -> None:
    assert await PostgresExclusionRecordRepository(database).get_many([]) == []


async def test_count_current_counts_only_this_sources_open_rows(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    assert await repository.count_current(SOURCE) == 0
    first_snapshot = await _snapshot(database)
    rows = [exclusion(snapshot_id=first_snapshot, last=name) for name in ("Aaname", "Bbname")]
    await repository.replace_current(SOURCE, first_snapshot, rows, NOW)
    assert await repository.count_current(SOURCE) == 2

    second_snapshot = await _snapshot(database)
    kept = exclusion(snapshot_id=second_snapshot, last="Aaname", raw=rows[0].raw)
    await repository.replace_current(SOURCE, second_snapshot, [kept], LATER)

    assert await repository.count_current(SOURCE) == 1
    assert await repository.count_current("test_other_list") == 0


RELATED_NPI = make_npi("100000002")


async def _business_and_owner(
    repository: PostgresExclusionRecordRepository, database: ConnectionSource
) -> tuple[ExclusionRecord, ExclusionRecord]:
    """A business row naming its owner and the owner's NPI, and the owner's own row."""
    snapshot_id = await _snapshot(database)
    business = exclusion(
        snapshot_id=snapshot_id,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Qzxsample Dentures NW",
        related_parties=("Qzxowner, Jordan L. DN",),
        npi=NPI,
        related_npis=(RELATED_NPI,),
    )
    owner = exclusion(snapshot_id=snapshot_id, last="Qzxowner", first="Jordan", npi=RELATED_NPI)
    await repository.replace_current(SOURCE, snapshot_id, [business, owner], NOW)
    return business, owner


async def test_find_candidates_by_related_npi_and_related_party(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    business, owner = await _business_and_owner(repository, database)

    by_npi = await repository.find_candidates(RELATED_NPI, None, None)
    by_name = await repository.find_candidates(None, "Qzxowner", None)

    assert _ours(by_npi) == {business.id, owner.id}
    assert _ours(by_name) == {business.id, owner.id}


async def test_search_finds_related_npis_and_parties_after_the_own_npi(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    business, owner = await _business_and_owner(repository, database)

    assert await _search_ids(repository, RELATED_NPI) == [owner.id, business.id]
    assert await _search_ids(repository, "qzxsample") == [business.id]
    assert await _search_ids(repository, "qzxowner jordan") == [owner.id, business.id]


async def test_get_a_record_current_or_not(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    business, owner = await _business_and_owner(repository, database)
    await repository.replace_current(SOURCE, business.snapshot_id, [business], LATER)

    assert await repository.get(business.id) == business
    closed = await repository.get(owner.id)
    assert closed is not None
    assert closed.valid_to == LATER
    assert await repository.get(uuid4()) is None


async def test_versions_of_one_identity_oldest_first(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    first_snapshot = await _snapshot(database)
    listed = exclusion(snapshot_id=first_snapshot, last="Qzxversioned")
    other = exclusion(snapshot_id=first_snapshot, last="Qzxbystander")
    await repository.replace_current(SOURCE, first_snapshot, [listed, other], NOW)
    second_snapshot = await _snapshot(database)
    ended = exclusion(snapshot_id=second_snapshot, last="Qzxversioned", end_date=date(2026, 1, 10))
    still = other.model_copy(update={"snapshot_id": second_snapshot})
    await repository.replace_current(SOURCE, second_snapshot, [ended, still], LATER)

    versions = await repository.versions(SOURCE, listed.record_hash)

    assert [(v.id, v.valid_from, v.valid_to) for v in versions] == [
        (listed.id, NOW, LATER),
        (ended.id, LATER, None),
    ]


async def test_linked_by_npi_matches_own_and_related_npis_of_current_rows(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    business, owner = await _business_and_owner(repository, database)

    linked = await repository.linked_by_npi([NPI, RELATED_NPI], exclude_id=business.id)
    naming = await repository.linked_by_npi([RELATED_NPI], exclude_id=owner.id)

    assert _ours(linked) == {owner.id}
    assert _ours(naming) == {business.id}
    assert await repository.linked_by_npi([], exclude_id=business.id) == []
    await repository.replace_current(SOURCE, business.snapshot_id, [business], LATER)
    assert _ours(await repository.linked_by_npi([RELATED_NPI], exclude_id=business.id)) == set()


async def test_same_name_is_bounded_and_puts_the_same_first_name_first(
    database: ConnectionSource,
) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    subject = exclusion(snapshot_id=snapshot_id, last="Qzxcommon", first="Avery")
    others = [
        exclusion(
            snapshot_id=snapshot_id,
            last="Qzxcommon",
            first="Blake",
            effective_date=date(2020, 1, 1) + timedelta(days=day),
        )
        for day in range(4)
    ]
    namesake = exclusion(
        snapshot_id=snapshot_id, last="QZXCOMMON", first="AVERY", effective_date=date(2019, 5, 5)
    )
    via_alias = exclusion(
        snapshot_id=snapshot_id, last="Qzxother", first="Casey", aliases=("Avery Qzxcommon",)
    )
    await repository.replace_current(
        SOURCE, snapshot_id, [subject, *others, namesake, via_alias], NOW
    )

    found = await repository.same_name(subject, limit=3)
    every = await repository.same_name(subject, limit=50)

    assert len(found) == 3
    assert found[0].id == namesake.id
    assert subject.id not in _ours(every)
    assert _ours(every) == {namesake.id, via_alias.id, *(r.id for r in others)}
    nameless = exclusion(snapshot_id=snapshot_id, last=None, organization_name=None)
    assert await repository.same_name(nameless, limit=10) == []


async def test_same_license_compares_number_and_state(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    licence = License(number="QZX 0098765", state="NY")
    subject = exclusion(snapshot_id=snapshot_id, last="Qzxholder", license=licence)
    same = exclusion(
        snapshot_id=snapshot_id, last="Qzxother", license=License(number="qzx0098765", state="NY")
    )
    other_state = exclusion(
        snapshot_id=snapshot_id, last="Qzxthird", license=License(number="QZX0098765", state="NJ")
    )
    await repository.replace_current(SOURCE, snapshot_id, [subject, same, other_state], NOW)

    assert _ours(await repository.same_license(subject, limit=10)) == {same.id}
    assert await repository.same_license(subject, limit=0) == []
    assert await repository.same_license(exclusion(snapshot_id=snapshot_id), limit=10) == []
