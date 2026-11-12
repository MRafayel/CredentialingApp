"""Use case: add a provider to an organization's roster and run its first check."""

import logging
from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from credentialing.application.errors import InvalidProviderInputError
from credentialing.application.recheck_providers import ADD_TRIGGER, FirstChecking
from credentialing.domain.enums import EntityKind, RosterRole
from credentialing.domain.errors import DuplicateNpiError
from credentialing.domain.models import Address, License, PersonName, Provider
from credentialing.domain.ports import Clock, ProviderRepository
from credentialing.domain.types import validate_npi

logger = logging.getLogger(__name__)


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
    role: RosterRole = RosterRole.PRACTITIONER


class RegisterProvider:
    """Validates provider data against the domain rules, stores the new provider and, with
    ``first_check``, checks it at once (every list and the NPI mirror), so the roster answers
    right away."""

    def __init__(
        self,
        providers: ProviderRepository,
        clock: Clock,
        *,
        first_check: FirstChecking | None = None,
    ) -> None:
        self._providers = providers
        self._clock = clock
        self._first_check = first_check

    async def run(self, organization_id: UUID, data: ProviderInput) -> Provider:
        """Create the provider under ``organization_id``, run its first check, and return it.
        A first check that fails is logged by error class; the provider stands, and its
        status reads "not checked".

        Raises:
            InvalidProviderInputError: See :meth:`build`.
            DuplicateNpiError: Another provider of the organization has this NPI; it names
                that provider when it was found first, and nothing is stored.
        """
        provider = self.build(organization_id, data, self._clock.now())
        if provider.npi is not None:
            holder = await self._providers.find_by_npi(organization_id, provider.npi)
            if holder is not None:
                raise DuplicateNpiError(organization_id, holder.id)
        await self._providers.add(provider)
        if self._first_check is not None:
            try:
                await self._first_check.run_for(organization_id, [provider.id], trigger=ADD_TRIGGER)
            except Exception as error:
                logger.warning(
                    "first check after adding failed organization_id=%s error=%s",
                    organization_id,
                    type(error).__name__,
                )
        return provider

    @staticmethod
    def build(organization_id: UUID, data: ProviderInput, now: datetime) -> Provider:
        """The new provider ``data`` describes, created at ``now``, without storing it: the
        rules every way of adding a provider goes through (one form, or a bulk import).

        Raises:
            InvalidProviderInputError: The entity kind is unknown, the NPI fails its check
                digit, or the data breaks a domain rule (an individual without a name).
        """
        if data.entity_kind is EntityKind.UNKNOWN:
            raise InvalidProviderInputError("entity_kind must be individual or organization")
        if data.npi is not None and not validate_npi(data.npi):
            raise InvalidProviderInputError("npi is not a valid ten-digit NPI")
        try:
            return Provider(
                id=uuid4(),
                organization_id=organization_id,
                created_at=now,
                **data.model_dump(),
            )
        except ValidationError as error:
            reasons = "; ".join(
                f"{'.'.join(str(part) for part in item['loc']) or 'provider'}: {item['msg']}"
                for item in error.errors()
            )
            raise InvalidProviderInputError(reasons) from error
