"""Providers and their stored monitoring status in Postgres, always scoped by organization.

``provider_status`` holds what the domain derived for each provider
(:func:`~credentialing.domain.policies.derive_status`), with the attention reason's rank as a
number, so a roster page is ordered, filtered and counted in SQL without reading anyone's
history: the rules stay in the domain, SQL only sorts by their output. Every statement filters
both tables by ``organization_id``; a status row's foreign key ties it to a provider of the
same organization. A roster holds each NPI once (the partial unique index
:data:`NPI_CONSTRAINT`); a write that breaks it raises :class:`DuplicateNpiError`.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime
from string.templatelib import Template
from typing import Final
from uuid import UUID

from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from credentialing.domain.enums import (
    Attention,
    AttentionScope,
    AttentionSelection,
    EntityKind,
    IdentityState,
    RosterOrder,
    RosterRole,
    Verdict,
)
from credentialing.domain.errors import DuplicateNpiError
from credentialing.domain.models import Address, License, PersonName, Provider, ProviderStatus
from credentialing.domain.policies import attention_rank
from credentialing.domain.types import Npi
from credentialing.infrastructure.database import ConnectionSource
from credentialing.infrastructure.repositories.jsonb import models_jsonb

NPI_CONSTRAINT: Final = "providers_organization_npi_key"
"""The unique index holding one NPI per organization's roster."""

_COLUMNS: Final = sql.SQL(
    "id, organization_id, entity_kind, last_name, first_name, middle_name, suffix,"
    " organization_name, npi, date_of_birth, licenses, addresses, taxonomy_codes, created_at,"
    " role, last_viewed_at"
)

_SELECT: Final = sql.SQL("select {columns} from providers").format(columns=_COLUMNS)

_PROVIDER_JOINED: Final = sql.SQL(
    "p.id, p.organization_id, p.entity_kind, p.last_name, p.first_name, p.middle_name,"
    " p.suffix, p.organization_name, p.npi, p.date_of_birth, p.licenses, p.addresses,"
    " p.taxonomy_codes, p.created_at, p.role, p.last_viewed_at"
)
"""A provider's columns in ``providers p left join provider_status s``."""

_JOINED: Final = sql.SQL(
    "{provider}, s.provider_id as status_provider_id, s.verdict, s.identity, s.changed,"
    " s.attention, s.last_checked_at, s.next_check_at, s.listed_in, s.flagged_in, s.review_in,"
    " s.unchecked, s.changed_at"
).format(provider=_PROVIDER_JOINED)
"""A provider's columns and its status's, from ``providers p left join provider_status s``."""

_FROM_JOINED: Final = sql.SQL(
    "providers p left join provider_status s"
    " on s.organization_id = p.organization_id and s.provider_id = p.id"
)

_UNRANKED: Final = attention_rank(None)
"""Where a provider without a stored status sorts: with those needing nobody."""

_ORDER: Final = {
    RosterOrder.ATTENTION: sql.SQL(
        "coalesce(s.attention_rank, {unranked}), p.created_at desc, p.id desc"
    ).format(unranked=sql.Literal(_UNRANKED)),
    RosterOrder.NEWEST: sql.SQL("p.created_at desc, p.id desc"),
    RosterOrder.OLDEST: sql.SQL("p.created_at, p.id"),
}
"""The ``order by`` of each :class:`RosterOrder`; every one ends with the id, so a page is
stable between the count and the read."""


@asynccontextmanager
async def _one_npi_per_roster(organization_id: UUID) -> AsyncIterator[None]:
    """Turn a violation of :data:`NPI_CONSTRAINT` inside the block into
    :class:`DuplicateNpiError`; any other unique violation propagates."""
    try:
        yield
    except UniqueViolation as error:
        if error.diag.constraint_name != NPI_CONSTRAINT:
            raise
        raise DuplicateNpiError(organization_id) from error


def _like_pattern(text: str) -> str:
    """A case-insensitive substring pattern with ``LIKE`` wildcards in ``text`` escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search(where: Template, query: str | None) -> Template:
    """``where`` narrowed to providers whose last, first or organization name contains
    ``query`` (case-insensitive) or whose NPI is ``query``; unchanged for a blank query. The
    name columns exist only on ``providers``, so this also serves the status join."""
    term = query.strip() if query else ""
    if not term:
        return where
    pattern = _like_pattern(term)
    return t"""{where:q} and (
        last_name ilike {pattern} or first_name ilike {pattern}
        or organization_name ilike {pattern} or npi = {term}
    )"""


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
    role: RosterRole
    last_viewed_at: datetime | None

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
            role=self.role,
            last_viewed_at=self.last_viewed_at,
        )


class _StatusRow(_ProviderRow):
    """A provider row joined with its ``provider_status`` row, absent (all null) when never
    stored."""

    status_provider_id: UUID | None
    verdict: Verdict | None
    identity: IdentityState | None
    changed: bool | None
    attention: Attention | None
    last_checked_at: datetime | None
    next_check_at: datetime | None
    listed_in: tuple[str, ...] | None
    flagged_in: tuple[str, ...] | None
    review_in: tuple[str, ...] | None
    unchecked: tuple[str, ...] | None
    changed_at: datetime | None

    def pair(self) -> tuple[Provider, ProviderStatus | None]:
        provider = self.to_domain()
        if self.status_provider_id is None or self.verdict is None or self.identity is None:
            return provider, None
        return provider, ProviderStatus(
            provider_id=self.status_provider_id,
            verdict=self.verdict,
            identity=self.identity,
            changed=bool(self.changed),
            attention=self.attention,
            last_checked_at=self.last_checked_at,
            next_check_at=self.next_check_at,
            listed_in=self.listed_in or (),
            flagged_in=self.flagged_in or (),
            review_in=self.review_in or (),
            unchecked=self.unchecked or (),
            changed_at=self.changed_at,
        )


def _insert_row(provider: Provider) -> dict[str, object]:
    """A new provider as one JSON object of :meth:`PostgresProviderRepository.add_many`'s
    record set, keyed by column."""
    name = provider.person_name
    return {
        "id": str(provider.id),
        "entity_kind": provider.entity_kind.value,
        "last_name": name.last if name else None,
        "first_name": name.first if name else None,
        "middle_name": name.middle if name else None,
        "suffix": name.suffix if name else None,
        "organization_name": provider.organization_name,
        "npi": provider.npi,
        "date_of_birth": provider.date_of_birth.isoformat() if provider.date_of_birth else None,
        "licenses": [item.model_dump(mode="json") for item in provider.licenses],
        "addresses": [item.model_dump(mode="json") for item in provider.addresses],
        "taxonomy_codes": list(provider.taxonomy_codes),
        "created_at": provider.created_at.isoformat(),
        "role": provider.role.value,
        "last_viewed_at": provider.last_viewed_at.isoformat() if provider.last_viewed_at else None,
    }


class PostgresProviderRepository:
    """Implements :class:`~credentialing.domain.ports.ProviderRepository`."""

    def __init__(self, database: ConnectionSource) -> None:
        self._database = database

    async def add(self, provider: Provider) -> None:
        """Store a new provider; :class:`DuplicateNpiError` when the organization has its NPI."""
        name = provider.person_name
        last = name.last if name else None
        first = name.first if name else None
        middle = name.middle if name else None
        suffix = name.suffix if name else None
        async with (
            _one_npi_per_roster(provider.organization_id),
            self._database.connection() as connection,
            connection.transaction(),
        ):
            await connection.execute(
                t"""
                insert into providers (
                    id, organization_id, entity_kind, last_name, first_name, middle_name,
                    suffix, organization_name, npi, date_of_birth, licenses, addresses,
                    taxonomy_codes, created_at, role, last_viewed_at
                ) values (
                    {provider.id}, {provider.organization_id}, {provider.entity_kind.value},
                    {last}, {first}, {middle}, {suffix}, {provider.organization_name},
                    {provider.npi}, {provider.date_of_birth}, {models_jsonb(provider.licenses)},
                    {models_jsonb(provider.addresses)}, {list(provider.taxonomy_codes)},
                    {provider.created_at}, {provider.role.value}, {provider.last_viewed_at}
                )
                """
            )

    async def add_many(self, organization_id: UUID, providers: Sequence[Provider]) -> None:
        """Store new providers of the organization in one statement, so all or none are
        stored; a provider of another organization refuses the whole call, and an NPI the
        organization or the batch already has raises :class:`DuplicateNpiError`."""
        if any(provider.organization_id != organization_id for provider in providers):
            raise ValueError("every provider must belong to the organization")
        if not providers:
            return
        rows = Jsonb([_insert_row(provider) for provider in providers])
        async with (
            _one_npi_per_roster(organization_id),
            self._database.connection() as connection,
            connection.transaction(),
        ):
            await connection.execute(
                t"""
                insert into providers (
                    id, organization_id, entity_kind, last_name, first_name, middle_name,
                    suffix, organization_name, npi, date_of_birth, licenses, addresses,
                    taxonomy_codes, created_at, role, last_viewed_at
                )
                select
                    x.id, {organization_id}, x.entity_kind, x.last_name, x.first_name,
                    x.middle_name, x.suffix, x.organization_name, x.npi, x.date_of_birth,
                    x.licenses, x.addresses, x.taxonomy_codes, x.created_at, x.role,
                    x.last_viewed_at
                from jsonb_to_recordset({rows}) as x(
                    id uuid, entity_kind text, last_name text, first_name text,
                    middle_name text, suffix text, organization_name text, npi text,
                    date_of_birth date, licenses jsonb, addresses jsonb, taxonomy_codes text[],
                    created_at timestamptz, role text, last_viewed_at timestamptz
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

    async def set_npi(self, organization_id: UUID, provider_id: UUID, npi: Npi) -> Provider | None:
        """Set the NPI of the organization's provider when it has none, in one statement, so
        two confirmations cannot both win; None when no row changed, and
        :class:`DuplicateNpiError` when another provider of the organization has the NPI."""
        async with (
            _one_npi_per_roster(organization_id),
            self._database.connection() as connection,
            connection.transaction(),
            connection.cursor(row_factory=class_row(_ProviderRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                update providers set npi = {npi}
                where organization_id = {organization_id} and id = {provider_id} and npi is null
                returning {_COLUMNS:q}
                """
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def list(
        self, organization_id: UUID, *, query: str | None, limit: int, offset: int
    ) -> tuple[Sequence[Provider], int]:
        """Return one page of the organization's providers, newest first, and the total."""
        where = _search(t"organization_id = {organization_id}", query)
        async with self._database.connection() as connection:
            count_cursor = await connection.execute(
                t"select count(*) from providers where {where:q}"
            )
            counted = await count_cursor.fetchone()
            async with connection.cursor(row_factory=class_row(_ProviderRow)) as cursor:
                await cursor.execute(
                    t"""
                    {_SELECT:q} where {where:q}
                    order by created_at desc, id desc limit {limit} offset {offset}
                    """
                )
                rows = await cursor.fetchall()
        total = int(counted[0]) if counted else 0
        return [row.to_domain() for row in rows], total

    async def list_status(
        self,
        organization_id: UUID,
        *,
        query: str | None,
        attention: AttentionSelection | None,
        kind: EntityKind | None = None,
        order: RosterOrder = RosterOrder.ATTENTION,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[tuple[Provider, ProviderStatus | None]], int]:
        """One page with each provider's stored status in the asked order (by default the
        stored attention rank, then newest first), and the total; two statements, both scoped
        to the organization on both tables (the join's condition and the provider filter)."""
        where = _search(t"p.organization_id = {organization_id}", query)
        if kind is not None:
            where = t"{where:q} and p.entity_kind = {kind.value}"
        match attention:
            case AttentionScope.ANY:
                where = t"{where:q} and s.attention is not null"
            case AttentionScope.NONE:
                where = t"{where:q} and s.attention is null"
            case Attention():
                where = t"{where:q} and s.attention = {attention.value}"
            case None:
                pass
        async with self._database.connection() as connection:
            count_cursor = await connection.execute(
                t"select count(*) from {_FROM_JOINED:q} where {where:q}"
            )
            counted = await count_cursor.fetchone()
            async with connection.cursor(row_factory=class_row(_StatusRow)) as cursor:
                await cursor.execute(
                    t"""
                    select {_JOINED:q} from {_FROM_JOINED:q} where {where:q}
                    order by {_ORDER[order]:q}
                    limit {limit} offset {offset}
                    """
                )
                rows = await cursor.fetchall()
        total = int(counted[0]) if counted else 0
        return [row.pair() for row in rows], total

    async def status_counts(self, organization_id: UUID) -> Mapping[Attention | None, int]:
        """Providers per stored attention reason, in one grouped statement."""
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                t"""
                select s.attention, count(*) from {_FROM_JOINED:q}
                where p.organization_id = {organization_id}
                group by s.attention
                """
            )
            rows = await cursor.fetchall()
        counts: dict[Attention | None, int] = {}
        for attention, count in rows:
            key = Attention(attention) if attention is not None else None
            counts[key] = counts.get(key, 0) + int(count)
        return counts

    async def save_statuses(
        self, organization_id: UUID, statuses: Sequence[ProviderStatus], *, rules_version: int
    ) -> None:
        """Upsert the statuses in one statement. A provider of another organization is never
        written: a new row's foreign key names the organization, and an existing row is only
        replaced when it belongs to ``organization_id``."""
        if not statuses:
            return
        rows = Jsonb(
            [
                {
                    **status.model_dump(mode="json"),
                    "attention_rank": attention_rank(status.attention),
                }
                for status in statuses
            ]
        )
        async with self._database.connection() as connection, connection.transaction():
            await connection.execute(
                t"""
                insert into provider_status (
                    provider_id, organization_id, verdict, identity, changed, changed_at,
                    attention, attention_rank, last_checked_at, next_check_at, listed_in,
                    flagged_in, review_in, unchecked, rules_version, updated_at
                )
                select
                    x.provider_id, {organization_id}, x.verdict, x.identity, x.changed,
                    x.changed_at, x.attention, x.attention_rank, x.last_checked_at,
                    x.next_check_at, x.listed_in, x.flagged_in, x.review_in, x.unchecked,
                    {rules_version}, now()
                from jsonb_to_recordset({rows}) as x(
                    provider_id uuid, verdict text, identity text, changed boolean,
                    changed_at timestamptz, attention text, attention_rank smallint,
                    last_checked_at timestamptz, next_check_at timestamptz, listed_in text[],
                    flagged_in text[], review_in text[], unchecked text[]
                )
                on conflict (provider_id) do update set
                    verdict = excluded.verdict, identity = excluded.identity,
                    changed = excluded.changed, changed_at = excluded.changed_at,
                    attention = excluded.attention,
                    attention_rank = excluded.attention_rank,
                    last_checked_at = excluded.last_checked_at,
                    next_check_at = excluded.next_check_at, listed_in = excluded.listed_in,
                    flagged_in = excluded.flagged_in, review_in = excluded.review_in,
                    unchecked = excluded.unchecked, rules_version = excluded.rules_version,
                    updated_at = excluded.updated_at
                where provider_status.organization_id = excluded.organization_id
                """
            )

    async def stale_statuses(self, organization_id: UUID, rules_version: int) -> Sequence[Provider]:
        """The organization's providers without a status of ``rules_version``, oldest first."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_ProviderRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                select {_PROVIDER_JOINED:q} from {_FROM_JOINED:q}
                where p.organization_id = {organization_id}
                  and (s.provider_id is null or s.rules_version <> {rules_version})
                order by p.created_at, p.id
                """
            )
            rows = await cursor.fetchall()
        return [row.to_domain() for row in rows]

    async def mark_viewed(
        self, organization_id: UUID, provider_id: UUID, at: datetime
    ) -> Provider | None:
        """Set ``last_viewed_at`` of the organization's provider; None when it has none."""
        async with (
            self._database.connection() as connection,
            connection.transaction(),
            connection.cursor(row_factory=class_row(_ProviderRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                update providers set last_viewed_at = {at}
                where organization_id = {organization_id} and id = {provider_id}
                returning {_COLUMNS:q}
                """
            )
            row = await cursor.fetchone()
        return row.to_domain() if row else None

    async def organization_ids(self) -> Sequence[UUID]:
        """Every organization's id, in id order."""
        async with self._database.connection() as connection:
            cursor = await connection.execute("select id from organizations order by id")
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def list_all(
        self, organization_id: UUID
    ) -> Sequence[tuple[Provider, ProviderStatus | None]]:
        """Every provider of the organization with its stored status, oldest first."""
        async with (
            self._database.connection() as connection,
            connection.cursor(row_factory=class_row(_StatusRow)) as cursor,
        ):
            await cursor.execute(
                t"""
                select {_JOINED:q} from {_FROM_JOINED:q}
                where p.organization_id = {organization_id}
                order by p.created_at, p.id
                """
            )
            rows = await cursor.fetchall()
        return [row.pair() for row in rows]
