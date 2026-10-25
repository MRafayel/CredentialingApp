"""Providers in Postgres, always scoped by organization."""

from datetime import date, datetime
from typing import Final
from uuid import UUID

from psycopg import sql
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import EntityKind
from credentialing.domain.models import Address, License, PersonName, Provider
from credentialing.domain.types import Npi
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import models_jsonb

_SELECT: Final = sql.SQL(
    "select id, organization_id, entity_kind, last_name, first_name, middle_name, suffix,"
    " organization_name, npi, date_of_birth, licenses, addresses, taxonomy_codes, created_at"
    " from providers"
)


class _ProviderRow(BaseModel):
    """One ``providers`` row, mapped into a :class:`Provider` by :meth:`to_domain`."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    organization_id: UUID
    entity_kind: EntityKind
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    suffix: str | None
    organization_name: str | None
    npi: str | None
    date_of_birth: date | None
    licenses: tuple[License, ...]
    addresses: tuple[Address, ...]
    taxonomy_codes: tuple[str, ...]
    created_at: datetime

    def to_domain(self) -> Provider:
        person_name = (
            PersonName(
                last=self.last_name,
                first=self.first_name or "",
                middle=self.middle_name,
                suffix=self.suffix,
            )
            if self.last_name is not None
            else None
        )
        return Provider(
            id=self.id,
            organization_id=self.organization_id,
            entity_kind=self.entity_kind,
            person_name=person_name,
            organization_name=self.organization_name,
            npi=self.npi,
            date_of_birth=self.date_of_birth,
            licenses=self.licenses,
            addresses=self.addresses,
            taxonomy_codes=self.taxonomy_codes,
            created_at=self.created_at,
        )


class PostgresProviderRepository:
    """Implements :class:`~credentialing.domain.ports.ProviderRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def add(self, provider: Provider) -> None:
        """Store a new provider."""
        name = provider.person_name
        last = name.last if name else None
        first = name.first if name else None
        middle = name.middle if name else None
        suffix = name.suffix if name else None
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                insert into providers (
                    id, organization_id, entity_kind, last_name, first_name, middle_name,
                    suffix, organization_name, npi, date_of_birth, licenses, addresses,
                    taxonomy_codes, created_at
                ) values (
                    {provider.id}, {provider.organization_id}, {provider.entity_kind.value},
                    {last}, {first}, {middle}, {suffix}, {provider.organization_name},
                    {provider.npi}, {provider.date_of_birth}, {models_jsonb(provider.licenses)},
                    {models_jsonb(provider.addresses)}, {list(provider.taxonomy_codes)},
                    {provider.created_at}
                )
                """
            )

    async def get(self, organization_id: UUID, provider_id: UUID) -> Provider | None:
        """Return the organization's provider with this id, if any."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ProviderRow)) as cursor,
        ):
            await cursor.execute(
                t"{_SELECT:q} where organization_id = {organization_id} and id = {provider_id}"
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def find_by_npi(self, organization_id: UUID, npi: Npi) -> Provider | None:
        """Return the organization's provider with this NPI (the earliest added), if any."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ProviderRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                {_SELECT:q} where organization_id = {organization_id} and npi = {npi}
                order by created_at, id limit 1
                """
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None
