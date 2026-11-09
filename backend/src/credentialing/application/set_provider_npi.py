"""Use case: the user confirms an NPI for a roster provider that has none.

The truth is NPPES, not the user: the NPI is looked up first (the mirror, and the live
registry when the mirror has no row or is stale), and set only when NPPES knows it. An NPI
nobody could check is never set. It is set once, in one conditional statement, so a provider
that has an NPI keeps it. The same lookup is then appended as an NPI verification, with who
set the NPI and when: the audit record, whose match says whether the registry's name agrees
with the roster's.
"""

from uuid import UUID

from credentialing.application.errors import (
    InvalidProviderInputError,
    ProviderHasNpiError,
    ProviderNotFoundError,
    RegistryUnavailableError,
)
from credentialing.application.npi_lookup import NpiLookup, NpiView
from credentialing.application.verify_npi import VerifyNpi
from credentialing.domain.enums import Outcome, RegistryStatus
from credentialing.domain.models import Provider, VerificationResult
from credentialing.domain.ports import Clock, ProviderRepository
from credentialing.domain.types import validate_npi


class SetProviderNpi:
    """Sets a provider's NPI once, after NPPES answered, and appends the verification."""

    def __init__(
        self,
        providers: ProviderRepository,
        npi_lookup: NpiLookup,
        verify_npi: VerifyNpi,
        clock: Clock,
    ) -> None:
        self._providers = providers
        self._lookup = npi_lookup
        self._verify = verify_npi
        self._clock = clock

    async def run(
        self, organization_id: UUID, provider_id: UUID, npi: str
    ) -> tuple[Provider, VerificationResult]:
        """Give the provider ``npi`` and append its NPI verification.

        Active, deactivated (it may be the provider's real, old NPI; the verification says
        so) and a mirror the live registry contradicts are set; the verification is the
        record of what NPPES said.

        Raises:
            InvalidProviderInputError: ``npi`` fails its check digit, or NPPES does not know it.
            ProviderNotFoundError: The organization has no provider with this id.
            ProviderHasNpiError: The provider has an NPI already (also when another request
                set one first).
            RegistryUnavailableError: NPPES could not be checked; nothing was set.
        """
        if not validate_npi(npi):
            raise InvalidProviderInputError("npi is not a valid ten-digit NPI")
        provider = await self._providers.get(organization_id, provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        if provider.npi is not None:
            raise ProviderHasNpiError(provider_id)
        view = await self._lookup.get(npi)
        if view.registry_status is RegistryStatus.NOT_FOUND:
            raise InvalidProviderInputError(f"NPI {npi} is not in NPPES")
        if view.registry_status is RegistryStatus.UNKNOWN:
            raise RegistryUnavailableError(_could_not_check(view))
        updated = await self._providers.set_npi(organization_id, provider_id, npi)
        if updated is None:
            raise ProviderHasNpiError(provider_id)
        result = await self._verify.run(
            organization_id,
            provider_id,
            details={"npi_set_by": "user", "npi_set_at": self._clock.now().isoformat()},
            view=view,
        )
        return updated, result


def _could_not_check(view: NpiView) -> str:
    """Why NPPES gave no answer: the live request's failure, or no way to ask at all."""
    live = view.live
    if live is not None and live.outcome is not Outcome.COMPLETED:
        return f"NPPES could not be checked: {live.note or live.outcome.value}"
    return "NPPES could not be checked: no live registry answer and no mirror load"
