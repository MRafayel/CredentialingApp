"""Provider roster endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import (
    ProviderCreate,
    ProviderPageResponse,
    ProviderResponse,
    VerdictResponse,
)
from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.list_providers import MAX_PAGE_SIZE

router = APIRouter(prefix="/providers", tags=["providers"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_provider(
    body: ProviderCreate, organization_id: OrganizationId, container: Container
) -> ProviderResponse:
    """Add a provider to the organization's roster."""
    provider = await container.register_provider.run(organization_id, body.to_input())
    return ProviderResponse.from_domain(provider)


@router.get("")
async def list_providers(
    organization_id: OrganizationId,
    container: Container,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> ProviderPageResponse:
    """One page of the organization's providers, newest first; ``q`` searches names or NPI."""
    result = await container.list_providers.run(
        organization_id, query=q, page=page, page_size=page_size
    )
    return ProviderPageResponse.from_page(result)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> ProviderResponse:
    """Return one of the organization's providers."""
    provider = await container.providers.get(organization_id, provider_id)
    if provider is None:
        raise ProviderNotFoundError(provider_id)
    return ProviderResponse.from_domain(provider)


@router.get("/{provider_id}/verdict")
async def provider_verdict(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> VerdictResponse:
    """The provider's verdict from the latest result of each source, with those results."""
    verdict = await container.provider_verdict.run(organization_id, provider_id)
    views = await container.verification_history.expand(verdict.results)
    return VerdictResponse.from_verdict(verdict, views)
