"""Verification endpoints: run checks and read the append-only history."""

from uuid import UUID

from fastapi import APIRouter

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import VerdictResponse, VerificationResponse

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
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> list[VerificationResponse]:
    """Every verification of the provider across credential types, newest first."""
    views = await container.verification_history.history(organization_id, provider_id)
    return [VerificationResponse.from_view(view) for view in views]
