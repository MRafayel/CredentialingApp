"""What route handlers depend on, assembled by the composition root and set on ``app.state``."""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request

from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.list_providers import ListProviders
from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.source_status import SourceStatusReport
from credentialing.application.verification_history import VerificationHistory
from credentialing.domain.ports import ProviderRepository


@dataclass(frozen=True, slots=True)
class AppContainer:
    """Use cases and read ports for the API, built once per process by ``bootstrap``.

    ``organization_header_enabled`` is true only in local development; see
    :func:`credentialing.api.organization.current_organization_id`.
    """

    register_provider: RegisterProvider
    list_providers: ListProviders
    source_status: SourceStatusReport
    check_exclusions: CheckExclusions
    lookup_exclusions: LookupExclusions
    verification_history: VerificationHistory
    provider_verdict: ProviderVerdictQuery
    providers: ProviderRepository
    organization_header_enabled: bool


def get_container(request: Request) -> AppContainer:
    """The container the composition root put on the application state."""
    return cast(AppContainer, request.app.state.container)


Container = Annotated[AppContainer, Depends(get_container)]
