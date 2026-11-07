"""The local NPI registry mirror: one NPI, or an exact search by NPI or name."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import NpiResponse, NpiSearchResponse
from credentialing.application.npi_lookup import MAX_RESULTS

router = APIRouter(prefix="/npi", tags=["npi"])


@router.get("/search")
async def search_npis(
    _organization_id: OrganizationId,
    container: Container,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 20,
) -> NpiSearchResponse:
    """NPIs of the mirror matching ``q`` exactly: ten digits as an NPI, otherwise a person's
    "last first" or "first last" name, one word as a last name, or an organization's name
    without legal designators. Active NPIs first. Prefix and fuzzy search are not offered yet.

    Every answer carries the NPPES load the mirror is as of. Fewer than two letters or digits
    search nothing.
    """
    result = await container.npi_lookup.search(q, limit=limit)
    return NpiSearchResponse.from_result(result)


@router.get("/{npi}")
async def get_npi(
    npi: Annotated[str, Path(pattern=r"^[0-9]{10}$")],
    _organization_id: OrganizationId,
    container: Container,
) -> NpiResponse:
    """One NPI as the mirror holds it, with the NPPES load the mirror is as of; 404 when the
    mirror has no record of it. Addresses are never returned (practice city and state only)."""
    view = await container.npi_lookup.get(npi)
    return NpiResponse.from_view(view)
