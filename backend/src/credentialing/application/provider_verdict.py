"""Use case: a provider's current verdict, rolled up from the latest result of each source."""

from uuid import UUID

from credentialing.application.errors import ProviderNotFoundError
from credentialing.domain.enums import CredentialType
from credentialing.domain.models import ProviderVerdict
from credentialing.domain.policies import derive_verdict
from credentialing.domain.ports import ProviderRepository, VerificationRepository


class ProviderVerdictQuery:
    """Reads the newest exclusion result of each source and derives the verdict from them."""

    def __init__(
        self, providers: ProviderRepository, verifications: VerificationRepository
    ) -> None:
        self._providers = providers
        self._verifications = verifications

    async def run(self, organization_id: UUID, provider_id: UUID) -> ProviderVerdict:
        """The provider's verdict; ``NOT_CHECKED`` when no check has run yet.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        if await self._providers.get(organization_id, provider_id) is None:
            raise ProviderNotFoundError(provider_id)
        latest = await self._verifications.latest_per_source(
            organization_id, provider_id, CredentialType.EXCLUSION
        )
        return derive_verdict(latest)
