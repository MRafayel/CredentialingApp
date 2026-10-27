from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from credentialing.domain.enums import EntityKind
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
