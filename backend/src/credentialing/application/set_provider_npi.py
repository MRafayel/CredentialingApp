"""Use case: the user confirms an NPI for a roster provider that has none.

The truth is NPPES, not the user: the NPI is looked up first (the mirror, and the live
registry when the mirror has no row or is stale), and set only when NPPES knows it. An NPI
nobody could check is never set, and an NPI another provider of the roster holds is refused
before NPPES is asked (a roster holds each NPI once). It is set once, in one conditional
statement, so a provider that has an NPI keeps it. The same lookup is then appended as an NPI
verification, with who set the NPI and when: the audit record, whose match says whether the
registry's name agrees with the roster's. Last, the provider is rechecked against the
exclusion lists at once (``npi_set``), since a published NPI is the one evidence that can make
a list match exact; a recheck that fails is logged by error class and organization id, and the
NPI and its verification stand.
"""

import logging
from uuid import UUID

from credentialing.application.errors import (
    InvalidProviderInputError,
    ProviderHasNpiError,
    ProviderNotFoundError,
    RegistryUnavailableError,
)
from credentialing.application.npi_lookup import NpiLookup, NpiView
from credentialing.application.recheck_providers import NPI_SET_TRIGGER, FirstChecking
from credentialing.application.verify_npi import VerifyNpi
from credentialing.domain.enums import Outcome, RegistryStatus
from credentialing.domain.errors import DuplicateNpiError
from credentialing.domain.models import Provider, VerificationResult
from credentialing.domain.ports import Clock, ProviderRepository
from credentialing.domain.types import validate_npi

logger = logging.getLogger(__name__)


class SetProviderNpi:
    """Sets a provider's NPI once, after NPPES answered, appends the verification and, with
    ``recheck``, rechecks the provider against the exclusion lists."""

    def __init__(
        self,
        providers: ProviderRepository,
        npi_lookup: NpiLookup,
        verify_npi: VerifyNpi,
        clock: Clock,
        *,
        recheck: FirstChecking | None = None,
    ) -> None:
        self._providers = providers
        self._lookup = npi_lookup
        self._verify = verify_npi
        self._clock = clock
        self._recheck = recheck

    async def run(
        self, organization_id: UUID, provider_id: UUID, npi: str
    ) -> tuple[Provider, VerificationResult]:
        """Give the provider ``npi``, append its NPI verification, then recheck the lists.

        Active, deactivated (it may be the provider's real, old NPI; the verification says
        so) and a mirror the live registry contradicts are set; the verification is the
        record of what NPPES said.

        Raises:
            InvalidProviderInputError: ``npi`` fails its check digit, or NPPES does not know it.
            ProviderNotFoundError: The organization has no provider with this id.
            ProviderHasNpiError: The provider has an NPI already (also when another request
                set one first).
            DuplicateNpiError: Another provider of the organization has this NPI (named when
                found before NPPES is asked; also when another request gave it first).
            RegistryUnavailableError: NPPES could not be checked; nothing was set.
        """
        if not validate_npi(npi):
            raise InvalidProviderInputError("npi is not a valid ten-digit NPI")
        provider = await self._providers.get(organization_id, provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        if provider.npi is not None:
            raise ProviderHasNpiError(provider_id)
        holder = await self._providers.find_by_npi(organization_id, npi)
        if holder is not None:
            raise DuplicateNpiError(organization_id, holder.id)
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
        if self._recheck is not None:
            try:
                await self._recheck.run_for(
                    organization_id, [provider_id], trigger=NPI_SET_TRIGGER, npi=False
                )
            except Exception as error:
                logger.warning(
                    "recheck after setting the npi failed organization_id=%s error=%s",
                    organization_id,
                    type(error).__name__,
                )
        return updated, result


def _could_not_check(view: NpiView) -> str:
    """Why NPPES gave no answer: the live request's failure, or no way to ask at all."""
    live = view.live
    if live is not None and live.outcome is not Outcome.COMPLETED:
        return f"NPPES could not be checked: {live.note or live.outcome.value}"
    return "NPPES could not be checked: no live registry answer and no mirror load"
