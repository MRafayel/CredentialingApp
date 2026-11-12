"""Provider roster endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from credentialing.api.container import Container
from credentialing.api.organization import OrganizationId
from credentialing.api.schemas import (
    AttentionFilter,
    ImportReportResponse,
    NpiSuggestionsResponse,
    ProviderCreate,
    ProviderImportBody,
    ProviderNpiBody,
    ProviderNpiResponse,
    ProviderPageResponse,
    ProviderResponse,
    ProviderStatusResponse,
    StatusSummaryResponse,
    VerdictResponse,
    VerificationResponse,
)
from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.list_providers import MAX_PAGE_SIZE

router = APIRouter(prefix="/providers", tags=["providers"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_provider(
    body: ProviderCreate, organization_id: OrganizationId, container: Container
) -> ProviderResponse:
    """Add a provider to the organization's roster. 409 with ``provider_id`` when another
    provider of the roster has the NPI (nothing is stored)."""
    provider = await container.register_provider.run(organization_id, body.to_input())
    return ProviderResponse.from_domain(provider)


@router.get("")
async def list_providers(
    organization_id: OrganizationId,
    container: Container,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
    attention: AttentionFilter | None = None,
) -> ProviderPageResponse:
    """One page of the organization's providers with their monitoring status, those needing
    attention first (most serious first), then newest first; ``q`` searches names or NPI,
    ``attention`` keeps only providers with that reason, ``any`` those with some reason,
    ``none`` those needing nobody."""
    result = await container.list_providers.run(
        organization_id,
        query=q,
        page=page,
        page_size=page_size,
        attention=attention.selection() if attention else None,
    )
    return ProviderPageResponse.from_page(result)


@router.get("/summary")
async def providers_summary(
    organization_id: OrganizationId, container: Container
) -> StatusSummaryResponse:
    """How many of the organization's providers have each attention reason (``none``: nothing
    needs a person), for the roster's filters, and the total."""
    summary = await container.provider_status_summary.run(organization_id)
    return StatusSummaryResponse.from_summary(summary)


@router.post("/import")
async def import_providers(
    body: ProviderImportBody, organization_id: OrganizationId, container: Container
) -> ImportReportResponse:
    """Read a pasted list or a CSV/TSV export into the organization's roster rows, each with
    its problems; with ``dry_run`` false, store the importable rows and run their first check
    now. 422 when the text is over 2 MB or 5,000 rows or cannot be read as delimited text, 409
    when another commit stored one of its NPIs meanwhile (nothing is stored either way)."""
    report = await container.import_providers.run(organization_id, body.text, dry_run=body.dry_run)
    return ImportReportResponse.from_report(report, dry_run=body.dry_run)


@router.get("/{provider_id}")
async def get_provider(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> ProviderResponse:
    """Return one of the organization's providers."""
    provider = await container.providers.get(organization_id, provider_id)
    if provider is None:
        raise ProviderNotFoundError(provider_id)
    return ProviderResponse.from_domain(provider)


@router.get("/{provider_id}/status")
async def provider_status(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> ProviderStatusResponse:
    """The provider's monitoring status, derived from its latest results now."""
    current = await container.provider_status.run(organization_id, provider_id)
    return ProviderStatusResponse.from_status(current)


@router.post("/{provider_id}/seen", status_code=status.HTTP_204_NO_CONTENT)
async def provider_seen(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> Response:
    """Record that the provider's page was opened: results changed before now no longer read
    as changed. 404 when the organization has no such provider."""
    await container.mark_viewed.run(organization_id, provider_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{provider_id}/verdict")
async def provider_verdict(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> VerdictResponse:
    """The provider's verdict from the latest result of each source, with those results."""
    verdict = await container.provider_verdict.run(organization_id, provider_id)
    views = await container.verification_history.expand(verdict.results)
    return VerdictResponse.from_verdict(verdict, views)


@router.get("/{provider_id}/npi-suggestions")
async def npi_suggestions(
    provider_id: UUID, organization_id: OrganizationId, container: Container
) -> NpiSuggestionsResponse:
    """NPIs from the registry mirror the provider may have, best first, for a provider
    without one; computed now, never stored, never EXACT by name. Nothing is written to the
    roster: the user confirms one with ``PUT /providers/{id}/npi``."""
    result = await container.suggest_npis.run(organization_id, provider_id)
    return NpiSuggestionsResponse.from_suggestions(result)


@router.put("/{provider_id}/npi")
async def set_provider_npi(
    provider_id: UUID, body: ProviderNpiBody, organization_id: OrganizationId, container: Container
) -> ProviderNpiResponse:
    """Set the NPI of a provider that has none, once NPPES knows it, append the NPI
    verification as the audit record, and recheck the provider against the exclusion lists
    at once (a recheck that fails leaves the NPI set). 422 when NPPES does not know the NPI,
    409 when the provider has an NPI already or another provider of the roster has this one
    (with its ``provider_id``), 503 when NPPES could not be checked (nothing is set)."""
    provider, result = await container.set_provider_npi.run(organization_id, provider_id, body.npi)
    (view,) = await container.verification_history.expand([result])
    return ProviderNpiResponse(
        provider=ProviderResponse.from_domain(provider),
        verification=VerificationResponse.from_view(view),
    )
