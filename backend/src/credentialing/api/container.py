"""What route handlers depend on, assembled by the composition root and set on ``app.state``."""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.import_providers import ImportProviders
from credentialing.application.list_providers import ListProviders
from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.npi_suggestions import SuggestNpis
from credentialing.application.provider_status import (
    MarkViewed,
    ProviderStatusQuery,
    ProviderStatusSummary,
)
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.application.record_profile import RecordProfileQuery
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.set_provider_npi import SetProviderNpi
from credentialing.application.source_status import SourceStatusReport
from credentialing.application.verification_history import VerificationHistory
from credentialing.application.verify_npi import VerifyNpi
from credentialing.domain.ports import ProviderRepository


@dataclass(frozen=True, slots=True)
class AppContainer:
    """Use cases and read ports for the API, built once per process by ``bootstrap``.

    ``organization_header_enabled`` is true only in local development; see
    :func:`credentialing.api.organization.current_organization_id`.
    """

    register_provider: RegisterProvider
    import_providers: ImportProviders
    list_providers: ListProviders
    source_status: SourceStatusReport
    check_exclusions: CheckExclusions
    lookup_exclusions: LookupExclusions
    record_profile: RecordProfileQuery
    verification_history: VerificationHistory
    provider_verdict: ProviderVerdictQuery
    provider_status: ProviderStatusQuery
    provider_status_summary: ProviderStatusSummary
    mark_viewed: MarkViewed
    npi_lookup: NpiLookup
    verify_npi: VerifyNpi
    suggest_npis: SuggestNpis
    set_provider_npi: SetProviderNpi
    providers: ProviderRepository
    organization_header_enabled: bool


def get_container(request: Request) -> AppContainer:
    """The container the composition root put on the application state."""
    return cast(AppContainer, request.app.state.container)


Container = Annotated[AppContainer, Depends(get_container)]
