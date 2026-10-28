"""Direct lookup of the exclusion lists, without a provider on the roster."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import LookupResponse, RecordProfileResponse
from credentialing.application.lookup_exclusions import MAX_RESULTS

router = APIRouter(prefix="/exclusions", tags=["exclusions"])


@router.get("/search")
async def search_exclusions(
    _organization_id: OrganizationId,
    container: Container,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 20,
) -> LookupResponse:
    """Current exclusion records matching a name, NPI or licence number, best first.

    Every list searched is returned with its dates, so an empty answer says what was searched
    and as of when. The lists are shared reference data, but the route asks for a caller like
    every other one. Fewer than two characters searches nothing.
    """
    result = await container.lookup_exclusions.run(q, limit=limit)
    return LookupResponse.from_result(result)


@router.get("/records/{record_id}")
async def exclusion_record_profile(
    record_id: UUID, _organization_id: OrganizationId, container: Container
) -> RecordProfileResponse:
    """One exclusion record and what the stored lists hold around it.

    Its versions in its list (what it said and since when), other records of the same party
    (same NPI or licence), records tied through the row's other NPIs, and records that only
    share the name. 404 when no record has this id.
    """
    profile = await container.record_profile.run(record_id)
    return RecordProfileResponse.from_profile(profile)
