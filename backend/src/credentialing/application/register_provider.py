"""Use case: add a provider to an organization's roster."""

from datetime import date
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from credentialing.application.errors import InvalidProviderInputError
from credentialing.domain.enums import EntityKind
from credentialing.domain.models import Address, License, PersonName, Provider
from credentialing.domain.ports import Clock, ProviderRepository
from credentialing.domain.types import validate_npi


class ProviderInput(BaseModel):
    """What a caller supplies to register a provider; ids and timestamps are assigned here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_kind: EntityKind
    person_name: PersonName | None = None
    organization_name: str | None = None
    npi: str | None = None
    date_of_birth: date | None = None
    licenses: tuple[License, ...] = ()
    addresses: tuple[Address, ...] = ()
    taxonomy_codes: tuple[str, ...] = ()


class RegisterProvider:
    """Validates provider data against the domain rules and stores the new provider."""

    def __init__(self, providers: ProviderRepository, clock: Clock) -> None:
        self._providers = providers
        self._clock = clock

    async def run(self, organization_id: UUID, data: ProviderInput) -> Provider:
        """Create the provider under ``organization_id`` and return it.

        Raises:
            InvalidProviderInputError: The entity kind is unknown, the NPI fails its check
                digit, or the data breaks a domain rule (an individual without a name).
        """
        if data.entity_kind is EntityKind.UNKNOWN:
            raise InvalidProviderInputError("entity_kind must be individual or organization")
        if data.npi is not None and not validate_npi(data.npi):
            raise InvalidProviderInputError("npi is not a valid ten-digit NPI")
        try:
            provider = Provider(
                id=uuid4(),
                organization_id=organization_id,
                created_at=self._clock.now(),
                **data.model_dump(),
            )
        except ValidationError as error:
            reasons = "; ".join(
                f"{'.'.join(str(part) for part in item['loc']) or 'provider'}: {item['msg']}"
                for item in error.errors()
            )
            raise InvalidProviderInputError(reasons) from error
        await self._providers.add(provider)
        return provider
