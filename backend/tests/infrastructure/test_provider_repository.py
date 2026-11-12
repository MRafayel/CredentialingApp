from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from credentialing.domain.enums import EntityKind, RosterRole
from credentialing.domain.errors import DuplicateNpiError
from credentialing.domain.models import Address, License, PersonName, Provider
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.providers import PostgresProviderRepository
from tests.synthetic import NOW, make_npi, organization

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NPI = make_npi("100000001")


async def _organization(connection: AsyncConnection[TupleRow]) -> UUID:
    organization_id = uuid4()
    await connection.execute(
        t"insert into organizations (id, name) values ({organization_id}, 'Testorg Clinic')"
    )
    return organization_id


def _provider(organization_id: UUID, npi: str | None = NPI) -> Provider:
    return Provider(
        id=uuid4(),
        organization_id=organization_id,
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Testperson", first="Avery", middle="J", suffix="Jr"),
        npi=npi,
        licenses=(License(number="0012345", state="NY", kind="Physician"),),
        addresses=(Address(line1="1 Sample Way", city="Sampleville", state="NY"),),
        taxonomy_codes=("207Q00000X",),
        created_at=NOW,
    )


async def test_add_then_get_round_trips(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    provider = _provider(await _organization(db_connection))

    await repository.add(provider)

    assert await repository.get(provider.organization_id, provider.id) == provider


async def test_organization_provider_round_trips(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    provider = organization().model_copy(
        update={"organization_id": await _organization(db_connection)}
    )

    await repository.add(provider)

    assert await repository.get(provider.organization_id, provider.id) == provider


async def test_reads_are_scoped_by_organization(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    provider = _provider(await _organization(db_connection))
    other_organization = await _organization(db_connection)
    await repository.add(provider)

    assert await repository.get(other_organization, provider.id) is None
    assert await repository.find_by_npi(other_organization, NPI) is None


async def test_add_many_stores_every_provider_within_the_organization(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    other_organization = await _organization(db_connection)
    person = _provider(organization_id).model_copy(
        update={"date_of_birth": date(1970, 2, 3), "role": RosterRole.WORKFORCE}
    )
    bare = Provider(
        id=uuid4(),
        organization_id=organization_id,
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Sampleperson", first="Blake"),
        created_at=NOW + timedelta(microseconds=1),
    )
    party = organization().model_copy(
        update={"organization_id": organization_id, "created_at": NOW + timedelta(seconds=1)}
    )

    await repository.add_many(organization_id, [person, bare, party])
    await repository.add_many(organization_id, [])

    stored = [provider for provider, _ in await repository.list_all(organization_id)]
    assert stored == [person, bare, party]
    assert await repository.list_all(other_organization) == []
    with pytest.raises(ValueError, match="belong to the organization"):
        await repository.add_many(other_organization, [_provider(organization_id)])
    assert len(await repository.list_all(organization_id)) == 3


async def test_find_by_npi(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    provider = _provider(organization_id)
    await repository.add(provider)
    await repository.add(_provider(organization_id, npi=None))

    assert await repository.find_by_npi(organization_id, NPI) == provider
    assert await repository.find_by_npi(organization_id, make_npi("100000002")) is None


async def test_set_npi_sets_it_once_within_the_organization(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    other_organization = await _organization(db_connection)
    provider = _provider(organization_id, npi=None)
    await repository.add(provider)
    other = make_npi("100000002")

    assert await repository.set_npi(other_organization, provider.id, NPI) is None
    updated = await repository.set_npi(organization_id, provider.id, NPI)
    again = await repository.set_npi(organization_id, provider.id, other)

    assert updated == provider.model_copy(update={"npi": NPI})
    assert again is None
    assert await repository.get(organization_id, provider.id) == updated
    assert await repository.set_npi(organization_id, uuid4(), NPI) is None


async def test_a_roster_holds_each_npi_once(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    other_organization = await _organization(db_connection)
    first = _provider(organization_id)
    await repository.add(first)

    with pytest.raises(DuplicateNpiError) as caught:
        await repository.add(_provider(organization_id))
    elsewhere = _provider(other_organization)
    await repository.add(elsewhere)
    await repository.add(_provider(organization_id, npi=None))
    await repository.add(_provider(organization_id, npi=None))

    assert (caught.value.organization_id, caught.value.existing_provider_id) == (
        organization_id,
        None,
    )
    assert str(caught.value) == "a provider with this NPI is already on the roster"
    npis = [p.npi for p, _ in await repository.list_all(organization_id)]
    assert sorted(npis, key=str) == [NPI, None, None]
    assert await repository.find_by_npi(organization_id, NPI) == first
    assert await repository.find_by_npi(other_organization, NPI) == elsewhere


async def test_add_many_with_a_held_npi_stores_none_of_the_batch(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    await repository.add(_provider(organization_id))
    fresh = _provider(organization_id, npi=make_npi("100000002"))

    with pytest.raises(DuplicateNpiError):
        await repository.add_many(organization_id, [fresh, _provider(organization_id)])
    with pytest.raises(DuplicateNpiError):
        await repository.add_many(
            organization_id, [fresh, fresh.model_copy(update={"id": uuid4()})]
        )

    assert [p.npi for p, _ in await repository.list_all(organization_id)] == [NPI]


async def test_set_npi_to_an_npi_another_provider_holds_changes_nothing(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    await repository.add(_provider(organization_id))
    without = _provider(organization_id, npi=None)
    await repository.add(without)

    with pytest.raises(DuplicateNpiError):
        await repository.set_npi(organization_id, without.id, NPI)

    assert await repository.get(organization_id, without.id) == without


async def test_list_pages_newest_first_and_searches(
    database: ConnectionSource, db_connection: AsyncConnection[TupleRow]
) -> None:
    repository = PostgresProviderRepository(database)
    organization_id = await _organization(db_connection)
    other_organization = await _organization(db_connection)
    oldest = _provider(organization_id).model_copy(update={"created_at": NOW - timedelta(days=2)})
    middle = _provider(organization_id, npi=None).model_copy(
        update={
            "person_name": PersonName(last="O'Searchperson", first="Blake"),
            "created_at": NOW - timedelta(days=1),
        }
    )
    newest = organization().model_copy(
        update={"organization_id": organization_id, "organization_name": "Sample 100% Care"}
    )
    for provider in (oldest, middle, newest, _provider(other_organization)):
        await repository.add(provider)

    everyone, total = await repository.list(organization_id, query=None, limit=2, offset=0)
    rest, _ = await repository.list(organization_id, query=None, limit=2, offset=2)
    beyond, beyond_total = await repository.list(organization_id, query="", limit=2, offset=10)

    assert total == 3
    assert [p.id for p in everyone] == [newest.id, middle.id]
    assert [p.id for p in rest] == [oldest.id]
    assert (list(beyond), beyond_total) == ([], 3)

    async def found(query: str) -> list[UUID]:
        items, count = await repository.list(organization_id, query=query, limit=10, offset=0)
        assert count == len(items)
        return [p.id for p in items]

    assert await found("  searchPERSON ") == [middle.id]
    assert await found("blake") == [middle.id]
    assert await found("100%") == [newest.id]
    assert await found("%") == [newest.id]
    assert await found("_") == []
    assert await found(NPI) == [oldest.id]
    assert await found(NPI[:4]) == []
