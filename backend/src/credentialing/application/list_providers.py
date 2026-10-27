"""Use case: one page of an organization's roster, optionally narrowed by a search."""

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from credentialing.domain.models import Provider
from credentialing.domain.ports import ProviderRepository

MAX_PAGE_SIZE: Final = 50
"""The largest page a caller may ask for."""


class ProviderPage(BaseModel):
    """One page of providers, newest first, with the total across all pages."""

    model_config = ConfigDict(frozen=True)

    items: tuple[Provider, ...]
    total: int
    page: int
    page_size: int


class ListProviders:
    """Pages through the roster; a search matches names or the exact NPI."""

    def __init__(self, providers: ProviderRepository) -> None:
        self._providers = providers

    async def run(
        self, organization_id: UUID, *, query: str | None, page: int, page_size: int
    ) -> ProviderPage:
        """Return page ``page`` (from 1) of ``page_size`` providers, capped at ``MAX_PAGE_SIZE``."""
        page = max(page, 1)
        page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
        term = query.strip() if query else None
        items, total = await self._providers.list(
            organization_id, query=term or None, limit=page_size, offset=(page - 1) * page_size
        )
        return ProviderPage(items=tuple(items), total=total, page=page, page_size=page_size)
