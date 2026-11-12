"""Use case: one page of an organization's monitored roster, optionally narrowed by a search or
by attention (one reason, any reason, or none)."""

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from credentialing.application.provider_status import ProviderStatusKeeper
from credentialing.domain.enums import AttentionSelection
from credentialing.domain.models import Provider, ProviderStatus
from credentialing.domain.ports import ProviderRepository

MAX_PAGE_SIZE: Final = 50
"""The largest page a caller may ask for."""


class ProviderWithStatus(BaseModel):
    """A roster provider and its monitoring status, as the backend decided it."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    status: ProviderStatus


class ProviderPage(BaseModel):
    """One page of providers, those needing attention first (by :class:`Attention` order),
    then newest first, with the total across all pages."""

    model_config = ConfigDict(frozen=True)

    items: tuple[ProviderWithStatus, ...]
    total: int
    page: int
    page_size: int


class ListProviders:
    """Pages through the roster with each provider's status; a search matches names or the
    exact NPI."""

    def __init__(self, providers: ProviderRepository, keeper: ProviderStatusKeeper) -> None:
        self._providers = providers
        self._keeper = keeper

    async def run(
        self,
        organization_id: UUID,
        *,
        query: str | None,
        page: int,
        page_size: int,
        attention: AttentionSelection | None = None,
    ) -> ProviderPage:
        """Return page ``page`` (from 1) of ``page_size`` providers, capped at
        ``MAX_PAGE_SIZE``; with ``attention``, only providers with that reason, or with
        :class:`~credentialing.domain.enums.AttentionScope` any reason or none.

        Statuses missing or derived by older rules are derived first, so the order and the
        filter rest on current rules; a provider added while the page was read gets its status
        derived on the spot.
        """
        page = max(page, 1)
        page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        term = query.strip() if query else None
        await self._keeper.refresh_stale(organization_id)
        rows, total = await self._providers.list_status(
            organization_id,
            query=term or None,
            attention=attention,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        items: list[ProviderWithStatus] = []
        for provider, status in rows:
            current = status if status is not None else await self._keeper.refresh(provider)
            items.append(ProviderWithStatus(provider=provider, status=current))
        return ProviderPage(items=tuple(items), total=total, page=page, page_size=page_size)
