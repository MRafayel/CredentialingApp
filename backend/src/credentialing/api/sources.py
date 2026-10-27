"""Source monitoring: every public source's links, last fetch, data age and next fetch."""

from fastapi import APIRouter

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import SourceStatusResponse

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
async def list_sources(
    _organization_id: OrganizationId, container: Container
) -> list[SourceStatusResponse]:
    """Every registered source's status, ordered by display name.

    The data is shared reference data, but the route asks for a caller like every other one.
    """
    statuses = await container.source_status.run()
    return [SourceStatusResponse.from_status(status) for status in statuses]
