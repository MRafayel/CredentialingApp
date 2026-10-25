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
