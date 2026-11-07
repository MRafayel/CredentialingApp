from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from credentialing.connectors.nppes import columns as c
from credentialing.connectors.nppes.deactivations import NppesDeactivationDataset
from credentialing.connectors.nppes.reader import NppesCsvDataset
from credentialing.domain.enums import DatasetLoadMode, EntityKind, NpiStatus, Outcome
from credentialing.domain.errors import DatasetFormatError, DatasetShrankError
from credentialing.domain.models import DatasetLoad, NpiRecord, PersonName, SourceSnapshot
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.npi_registry import PostgresNpiRegistryRepository
from credentialing.infrastructure.repositories.snapshots import PostgresSnapshotRepository
from tests.connectors.fixtures import nppes_builders as b
from tests.fakes import ListDataset
from tests.synthetic import NOW, make_npi

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

LATER = NOW + timedelta(days=30)
EVEN_LATER = NOW + timedelta(days=60)


class _Mirror:
    """The repository with a snapshot per load and files written to a temporary folder."""

    def __init__(self, database: ConnectionSource, folder: Path) -> None:
        self.repository = PostgresNpiRegistryRepository(database)
        self._snapshots = PostgresSnapshotRepository(database)
        self._database = database
        self._folder = folder

    async def snapshot(self) -> UUID:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id="test_nppes",
            fetched_at=NOW,
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/nppes.zip",
        )
        await self._snapshots.add(snapshot)
        return snapshot.id

    async def load_rows(
        self,
        rows: list[dict[str, str]],
        *,
        mode: DatasetLoadMode = DatasetLoadMode.FULL,
        now: datetime = NOW,
        min_rows: int | None = None,
    ) -> DatasetLoad:
        snapshot_id = await self.snapshot()
        path = self._folder / f"{uuid4()}.zip"
        path.write_bytes(b.nppes_zip(rows))
        dataset = NppesCsvDataset(path, snapshot_id)
        try:
            return await self.repository.load(
                dataset,
                mode=mode,
                snapshot_id=snapshot_id,
                now=now,
                min_rows=min_rows,
            )
        finally:
            dataset.close()

    async def history(self, npi: str) -> list[tuple[str, object, object, bool]]:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select line_hash, valid_from, valid_to, in_file from npi_registry_history
                where npi = {npi} order by valid_to
                """
            )
            return [(row[0], row[1], row[2], row[3]) for row in await cursor.fetchall()]

    async def history_count(self) -> int:
        async with self._database.connection() as connection:
            cursor = await connection.execute("select count(*) from npi_registry_history")
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


@pytest.fixture
async def mirror(database: ConnectionSource, tmp_path: Path) -> _Mirror:
    """The repository over an empty mirror.

    The development database holds the real registry (ten million rows). Inside the test's
    rolled-back transaction the tables are truncated first: a full-file load would otherwise
    flag every real row as missing, which takes minutes and leaves millions of dead tuples
    behind when the transaction rolls back. Truncation is transactional and O(1) in Postgres,
    so the real rows are back untouched once the test ends. The NPI links refer to the
    mirror, so they are truncated with it.
    """
    async with database.connection() as connection:
        await connection.execute("truncate npi_registry, npi_registry_history, exclusion_npi_links")
    return _Mirror(database, tmp_path)


async def test_a_first_load_adds_every_row_and_get_round_trips_every_field(
    mirror: _Mirror, tmp_path: Path
) -> None:
    rows = b.monthly_rows()

    load = await mirror.load_rows(rows)

    assert load.added == len(rows)
    assert (load.changed, load.unchanged, load.missing) == (0, 0, 0)
    assert list(load.phases) == ["scan", "compare", "read", "apply"]
    path = tmp_path / "expected.zip"
    path.write_bytes(b.nppes_zip(rows))
    expected = NppesCsvDataset(path, uuid4())
    for record in expected.records(None):
        stored = await mirror.repository.get(record.npi)
        assert stored is not None
        assert stored.first_seen_at == NOW
        assert stored.updated_at == NOW
        assert (
            stored.model_copy(
                update={
                    "snapshot_id": record.snapshot_id,
                    "first_seen_at": None,
                    "updated_at": None,
                }
            )
            == record
        )
    expected.close()
    assert await mirror.repository.count() == len(rows)
    assert await mirror.repository.count(status=NpiStatus.DEACTIVATED) == 1
    assert await mirror.repository.get(make_npi("199999999")) is None


async def test_loading_the_same_file_again_writes_nothing(mirror: _Mirror) -> None:
    await mirror.load_rows(b.monthly_rows())

    again = await mirror.load_rows(b.monthly_rows(), now=LATER)

    assert again.unchanged == again.scanned == 6
    assert (again.added, again.changed, again.missing) == (0, 0, 0)
    assert "read" not in again.phases
    assert await mirror.history_count() == 0
    stored = await mirror.repository.get(b.PERSON_NPI)
    assert stored is not None
    assert stored.updated_at == NOW


async def test_a_changed_row_is_versioned_and_replaced(mirror: _Mirror) -> None:
    await mirror.load_rows([b.person(), b.organization()])
    before = await mirror.repository.get(b.PERSON_NPI)
    moved = b.person(**{c.PRACTICE_CITY: "NEWTOWN", c.LAST_UPDATE_DATE: "01/02/2026"})

    load = await mirror.load_rows([moved, b.organization()], now=LATER)

    assert (load.added, load.changed, load.unchanged) == (0, 1, 1)
    after = await mirror.repository.get(b.PERSON_NPI)
    assert before is not None
    assert after is not None
    assert after.practice_address is not None
    assert after.practice_address.city == "NEWTOWN"
    assert (after.first_seen_at, after.updated_at) == (NOW, LATER)
    assert after.snapshot_id != before.snapshot_id
    assert await mirror.history(b.PERSON_NPI) == [(before.line_hash, NOW, LATER, True)]


async def test_a_full_file_flags_rows_it_no_longer_lists_and_they_can_return(
    mirror: _Mirror,
) -> None:
    await mirror.load_rows([b.person(), b.organization()])

    dropped = await mirror.load_rows([b.organization()], now=LATER)
    flagged = await mirror.repository.get(b.PERSON_NPI)
    back = await mirror.load_rows([b.person(), b.organization()], now=EVEN_LATER)

    assert dropped.missing == 1
    assert flagged is not None
    assert (flagged.in_file, flagged.updated_at) == (False, LATER)
    assert back.changed == 1
    assert back.added == 0
    returned = await mirror.repository.get(b.PERSON_NPI)
    assert returned is not None
    assert returned.in_file is True
    assert [entry[3] for entry in await mirror.history(b.PERSON_NPI)] == [True, False]
    assert await mirror.repository.count() == 2


async def test_an_incremental_file_adds_and_changes_but_never_flags(mirror: _Mirror) -> None:
    await mirror.load_rows([b.person(), b.organization()])
    renamed = b.organization(
        **{c.ORGANIZATION_NAME: "SAMPLEVILLE CARE GROUP PLLC", c.LAST_UPDATE_DATE: "01/02/2026"}
    )

    load = await mirror.load_rows(
        [renamed, b.reactivated()], mode=DatasetLoadMode.INCREMENTAL, now=LATER
    )

    assert (load.added, load.changed, load.missing) == (1, 1, 0)
    person = await mirror.repository.get(b.PERSON_NPI)
    assert person is not None
    assert person.in_file
    organization = await mirror.repository.get(b.ORGANIZATION_NPI)
    assert organization is not None
    assert organization.organization_name == "SAMPLEVILLE CARE GROUP PLLC"


async def test_a_row_older_than_the_mirror_is_left_alone(mirror: _Mirror) -> None:
    await mirror.load_rows([b.person(**{c.LAST_UPDATE_DATE: "01/02/2026"})])
    older = b.person(**{c.PRACTICE_CITY: "OLDTOWN", c.LAST_UPDATE_DATE: "12/01/2025"})

    load = await mirror.load_rows([older], mode=DatasetLoadMode.INCREMENTAL, now=LATER)

    assert (load.outdated, load.changed, load.added) == (1, 0, 0)
    stored = await mirror.repository.get(b.PERSON_NPI)
    assert stored is not None
    assert stored.last_updated == date(2026, 1, 2)
    assert await mirror.history_count() == 0


async def test_a_repeated_npi_keeps_its_last_row(mirror: _Mirror) -> None:
    first = b.person(**{c.PRACTICE_CITY: "FIRSTTOWN"})
    last = b.person(**{c.PRACTICE_CITY: "LASTTOWN"})

    load = await mirror.load_rows([first, b.organization(), last])

    assert (load.scanned, load.duplicates, load.added) == (3, 1, 2)
    stored = await mirror.repository.get(b.PERSON_NPI)
    assert stored is not None
    assert stored.practice_address is not None
    assert stored.practice_address.city == "LASTTOWN"


async def test_a_deactivation_file_sets_status_and_date_only(
    mirror: _Mirror, tmp_path: Path
) -> None:
    await mirror.load_rows([b.person(), b.reactivated()])
    before = await mirror.repository.get(b.PERSON_NPI)
    unknown = make_npi("150000088")
    path = tmp_path / "deactivations.zip"
    path.write_bytes(
        b.deactivation_zip(
            rows=(
                (b.PERSON_NPI, "01/05/2026"),
                (unknown, "06/15/2019"),
                (b.REACTIVATED_NPI, "02/01/2020"),
            )
        )
    )
    snapshot_id = await mirror.snapshot()
    dataset = NppesDeactivationDataset(path, snapshot_id)

    load = await mirror.repository.load(
        dataset, mode=DatasetLoadMode.DEACTIVATIONS, snapshot_id=snapshot_id, now=LATER
    )

    assert (load.added, load.changed, load.deactivated, load.outdated) == (1, 1, 2, 1)
    assert list(load.phases) == ["scan", "read", "compare", "apply"]
    person = await mirror.repository.get(b.PERSON_NPI)
    assert before is not None
    assert person is not None
    assert (person.status, person.deactivation_date) == (NpiStatus.DEACTIVATED, date(2026, 1, 5))
    assert person.line_hash == before.line_hash
    assert person.person_name == before.person_name
    assert person.taxonomies == before.taxonomies
    assert len(await mirror.history(b.PERSON_NPI)) == 1
    stub = await mirror.repository.get(unknown)
    assert stub is not None
    assert (stub.entity_kind, stub.status) == (EntityKind.UNKNOWN, NpiStatus.DEACTIVATED)
    reactivated = await mirror.repository.get(b.REACTIVATED_NPI)
    assert reactivated is not None
    assert reactivated.status is NpiStatus.ACTIVE
    again = await mirror.repository.load(
        dataset, mode=DatasetLoadMode.DEACTIVATIONS, snapshot_id=snapshot_id, now=EVEN_LATER
    )
    assert (again.deactivated, again.unchanged) == (0, 2)


async def test_a_load_that_fails_mid_way_leaves_the_mirror_as_it_was(mirror: _Mirror) -> None:
    await mirror.load_rows([b.person()])
    changed = NpiRecord(
        npi=b.PERSON_NPI,
        entity_kind=EntityKind.INDIVIDUAL,
        status=NpiStatus.ACTIVE,
        person_name=PersonName(last="CHANGED", first="AVERY"),
        line_hash="ff" * 32,
        snapshot_id=uuid4(),
    )
    dataset = ListDataset(changed, fail_reading=DatasetFormatError("line 2: synthetic"))
    snapshot_id = await mirror.snapshot()

    with pytest.raises(DatasetFormatError):
        await mirror.repository.load(
            dataset, mode=DatasetLoadMode.FULL, snapshot_id=snapshot_id, now=LATER
        )

    stored = await mirror.repository.get(b.PERSON_NPI)
    assert stored is not None
    assert stored.person_name is not None
    assert stored.person_name.last == "TESTPERSON"
    assert await mirror.history_count() == 0


async def test_a_file_below_the_minimum_is_refused_before_any_write(mirror: _Mirror) -> None:
    await mirror.load_rows([b.person(), b.organization()])

    with pytest.raises(DatasetShrankError) as raised:
        await mirror.load_rows([b.organization()], now=LATER, min_rows=2)

    assert (raised.value.rows, raised.value.minimum) == (1, 2)
    flagged = await mirror.repository.get(b.PERSON_NPI)
    assert flagged is not None
    assert flagged.in_file


async def test_text_that_copy_must_escape_round_trips(mirror: _Mirror) -> None:
    awkward = b.organization(
        **{
            c.ORGANIZATION_NAME: "TAB\tAND\\BACKSLASH",
            c.OTHER_ORGANIZATION_NAME: 'QUOTE " AND {BRACE}, COMMA',
            c.PRACTICE_LINE1: "LINE\r\nBREAK",
        }
    )

    await mirror.load_rows([awkward])

    stored = await mirror.repository.get(b.ORGANIZATION_NPI)
    assert stored is not None
    assert stored.organization_name == "TAB\tAND\\BACKSLASH"
    assert stored.other_names == ('QUOTE " AND {BRACE}, COMMA', "SAMPLEVILLE HEALTH HOLDINGS")
    assert stored.practice_address is not None
    assert stored.practice_address.line1 == "LINE\r\nBREAK"
