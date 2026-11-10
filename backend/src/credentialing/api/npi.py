"""The NPI registry: one NPI, or a search, from the local mirror and on demand the live registry."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import NpiLookupResponse, NpiSearchResponse
from credentialing.application.npi_lookup import LIVE_DEFAULT_RESULTS, LIVE_MAX_RESULTS
from credentialing.domain.enums import EntityKind
from credentialing.domain.ports import NpiQuery

router = APIRouter(prefix="/npi", tags=["npi"])

_MIRROR_DEFAULT_RESULTS = 20

_Name = Annotated[str | None, Query(max_length=100)]


@router.get("/search")
async def search_npis(
    _organization_id: OrganizationId,
    container: Container,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int | None, Query(ge=1, le=LIVE_MAX_RESULTS)] = None,
    live: bool = False,
    last_name: _Name = None,
    first_name: _Name = None,
    organization_name: _Name = None,
    entity_kind: EntityKind | None = None,
    state: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    city: _Name = None,
    postal_code: Annotated[str | None, Query(max_length=10)] = None,
) -> NpiSearchResponse:
    """NPIs of the mirror matching ``q`` as typed, or with ``live`` of the live registry.

    Mirror (default): ``q`` as ten digits (an NPI), a person's "last first" or "first last"
    name whose last word is the start of a name part (as a last name from two characters;
    every word must fit, inner words the middle name), one word as the start of a last name,
    or an organization's whole name without legal designators; active NPIs first; at most 50
    (20 by default). Fewer than two letters or digits search nothing. No fuzzy search.

    Live: ``q`` is ignored and the structured fields are sent to the NPPES API in one request
    (a last name or an organization name is required; a name may end with ``*`` after two
    characters); at most 200 (50 by default); no paging. Every result newer than the mirror
    is stored in it. A failed request has no records and says why in ``live``, never mirror
    rows in their place. A query the registry would refuse is 422.
    """
    if not live:
        result = await container.npi_lookup.search(q, limit=limit or _MIRROR_DEFAULT_RESULTS)
        return NpiSearchResponse.from_result(result)
    try:
        query = NpiQuery(
            last_name=last_name,
            first_name=first_name,
            organization_name=organization_name,
            entity_kind=entity_kind,
            state=state,
            city=city,
            postal_code=postal_code,
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    result = await container.npi_lookup.search(
        limit=limit or LIVE_DEFAULT_RESULTS, live=True, query=query
    )
    return NpiSearchResponse.from_result(result)


@router.get("/{npi}")
async def get_npi(
    npi: Annotated[str, Path(pattern=r"^[0-9]{10}$")],
    _organization_id: OrganizationId,
    container: Container,
    live: bool = False,
) -> NpiLookupResponse:
    """One NPI: the backend's ``status``, its record, the mirror's load and the live check.

    The mirror is read first; the live registry is asked when ``live``, when the mirror has
    no row, or when its load is stale (one request; a newer answer is stored in the mirror).
    Always 200 for ten digits: "not in NPPES" (``not_found``) and "could not check"
    (``unknown``, with the failure in ``live``) are answers. Addresses are never returned
    (practice city and state only).
    """
    view = await container.npi_lookup.get(npi, live=live)
    return NpiLookupResponse.from_view(view)
