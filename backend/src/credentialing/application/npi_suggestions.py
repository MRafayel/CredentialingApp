"""Use case: NPI suggestions for a roster provider without an NPI, from the registry mirror.

The provider becomes a :class:`~credentialing.domain.models.LinkParty`, the mirror is searched
with the same discovery as exclusion records, and every candidate is scored by the stage 2
linking rules (:func:`~credentialing.domain.policies.suggest_npis`): never EXACT by name, best
first, and one ``NONE`` row saying why when nothing fits. Suggestions are computed on request
and never stored; nothing is written to the roster (the user confirms an NPI through
:class:`~credentialing.application.set_provider_npi.SetProviderNpi`). Every answer carries the
mirror's provenance: which NPPES load it is as of, and how fresh.
"""

from typing import Final
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.npi_lookup import MirrorSource, NpiLookup
from credentialing.domain.enums import MatchLevel
from credentialing.domain.models import MatchResult, NpiRecord
from credentialing.domain.policies import party_of_provider, suggest_npis
from credentialing.domain.ports import Clock, NpiLinkRepository, ProviderRepository
from credentialing.domain.types import Npi

HAS_NPI_NOTE: Final = "the provider already has an NPI; verify it with POST .../verifications/npi"

MIRROR_NOT_LOADED: Final = "No match: the NPI registry mirror is not loaded."


class NpiSuggestion(BaseModel):
    """One NPI the provider may have, with the match behind it and the mirror row; ``npi`` and
    ``record`` are None on the one row saying why nothing was suggested."""

    model_config = ConfigDict(frozen=True)

    npi: Npi | None
    record: NpiRecord | None
    match: MatchResult


class NpiSuggestions(BaseModel):
    """The suggestions for one provider, best first, and where they came from.

    ``mirror`` is the NPPES load the mirror is as of (None when never loaded); ``found`` how
    many mirror rows the discovery found (``CANDIDATE_CAP + 1`` meaning more than the cap);
    ``note`` says why no discovery ran for a provider that already has an NPI.
    """

    model_config = ConfigDict(frozen=True)

    provider_id: UUID
    computed_at: AwareDatetime
    mirror: MirrorSource | None
    found: int
    suggestions: tuple[NpiSuggestion, ...]
    note: str | None = None


class SuggestNpis:
    """Suggests NPIs from the mirror for a provider without one; reads only."""

    def __init__(
        self,
        providers: ProviderRepository,
        npi_links: NpiLinkRepository,
        npi_lookup: NpiLookup,
        clock: Clock,
    ) -> None:
        self._providers = providers
        self._links = npi_links
        self._lookup = npi_lookup
        self._clock = clock

    async def run(self, organization_id: UUID, provider_id: UUID) -> NpiSuggestions:
        """The organization's provider's NPI suggestions, computed now.

        A provider with an NPI gets none (its NPI is verified, not searched for). Without a
        loaded mirror the answer is one ``NONE`` row saying so, never an empty list.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        provider = await self._providers.get(organization_id, provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        computed_at = self._clock.now()
        mirror = await self._lookup.mirror()
        if provider.npi is not None:
            return NpiSuggestions(
                provider_id=provider_id,
                computed_at=computed_at,
                mirror=mirror,
                found=0,
                suggestions=(),
                note=HAS_NPI_NOTE,
            )
        if mirror is None:
            unloaded = MatchResult(
                level=MatchLevel.NONE, evidence=(), explanation=MIRROR_NOT_LOADED
            )
            return NpiSuggestions(
                provider_id=provider_id,
                computed_at=computed_at,
                mirror=None,
                found=0,
                suggestions=(NpiSuggestion(npi=None, record=None, match=unloaded),),
            )
        party = party_of_provider(provider)
        candidates, found = await self._links.candidates_for(party)
        rows = {candidate.npi: candidate for candidate in candidates}
        return NpiSuggestions(
            provider_id=provider_id,
            computed_at=computed_at,
            mirror=mirror,
            found=found,
            suggestions=tuple(
                NpiSuggestion(npi=npi, record=rows.get(npi) if npi else None, match=match)
                for npi, match in suggest_npis(party, candidates, found)
            ),
        )
