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


async def test_find_candidates_ignores_rows_no_longer_listed(database: ConnectionSource) -> None:
    repository = PostgresExclusionRecordRepository(database)
    snapshot_id = await _snapshot(database)
    await repository.replace_current(SOURCE, snapshot_id, [exclusion(snapshot_id=snapshot_id)], NOW)
    await repository.replace_current(SOURCE, await _snapshot(database), [], LATER)

    assert _ours(await repository.find_candidates(None, "Testperson", None)) == set()


async def test_get_many_with_no_ids(database: ConnectionSource) -> None:
    assert await PostgresExclusionRecordRepository(database).get_many([]) == []
