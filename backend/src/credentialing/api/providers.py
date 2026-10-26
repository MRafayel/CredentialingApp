"""Provider roster endpoints."""

from uuid import UUID

from fastapi import APIRouter, status

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import ProviderCreate, ProviderResponse
from credentialing.application.errors import ProviderNotFoundError

router = APIRouter(prefix="/providers", tags=["providers"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_provider(
    body: ProviderCreate, organization_id: OrganizationId, container: Container
) -> ProviderResponse:
    """Add a provider to the organization's roster."""
    provider = await container.register_provider.run(organization_id, body.to_input())
    return ProviderResponse.from_domain(provider)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> ProviderResponse:
    """Return one of the organization's providers."""
    provider = await container.providers.get(organization_id, provider_id)
    if provider is None:
        raise ProviderNotFoundError(provider_id)
    return ProviderResponse.from_domain(provider)
