"""Verification endpoints: run checks and read the append-only history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import HistoryPageResponse, VerdictResponse, VerificationResponse
from credentialing.application.verification_history import DEFAULT_MOMENTS, MAX_MOMENTS

router = APIRouter(prefix="/providers/{provider_id}/verifications", tags=["verifications"])


@router.post("/exclusions")
async def check_exclusions(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> VerdictResponse:
    """Check the provider against every exclusion list covering them; the verdict they give."""
    verdict = await container.check_exclusions.run(organization_id, provider_id)
    views = await container.verification_history.expand(verdict.results)
    return VerdictResponse.from_verdict(verdict, views)


@router.post("/npi")
async def verify_npi(
    provider_id: UUID, organization_id: OrganizationId, container: Container, live: bool = False
) -> VerificationResponse:
    """Verify the provider's NPI in NPPES and append the result to the history.

    The mirror answers unless ``live``, the mirror has no row, or its load is stale; then the
    live registry is asked once. Active, deactivated, not in NPPES, a mirror the live registry
    contradicts (review), no NPI on file (a manual step) and a check that could not run are
    each their own result; the exclusion verdict is not changed.
    """
    result = await container.verify_npi.run(organization_id, provider_id, live=live)
    (view,) = await container.verification_history.expand([result])
    return VerificationResponse.from_view(view)


@router.get("")
async def verification_history(
    provider_id: UUID,
    organization_id: OrganizationId,
    container: Container,
    before: Annotated[AwareDatetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_MOMENTS)] = DEFAULT_MOMENTS,
) -> HistoryPageResponse:
    """The provider's verifications across credential types, newest first, ``limit`` moments
    at a time.

    A moment is one instant results were stored at: a check run stores one result per list at
    the same instant, an NPI verification has its own, so a page never splits a run. Pass the
    page's ``next_before`` as ``before`` for the next one (null: no older history). History
    goes back ``retention_days``, except each list's and the NPI's newest result.
    """
    page = await container.verification_history.page(
        organization_id, provider_id, before=before, moments=limit
    )
    return HistoryPageResponse.from_page(page)


@router.get("/npi/latest")
async def latest_npi_verification(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> VerificationResponse | None:
    """The provider's newest NPI verification from any source (null: none yet), which can be
    older than the history's first page."""
    view = await container.verification_history.latest_npi(organization_id, provider_id)
    return VerificationResponse.from_view(view) if view else None
