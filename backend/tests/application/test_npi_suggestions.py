"""NPI suggestions for a roster provider without an NPI: computed from the mirror, never stored."""

from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.npi_suggestions import (
    HAS_NPI_NOTE,
    MIRROR_NOT_LOADED,
    NpiSuggestions,
    SuggestNpis,
)
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import DatasetLoadMode, Freshness, MatchLevel, Outcome
from credentialing.domain.models import (
    Address,
    DatasetLoad,
    License,
    NpiRecord,
    Provider,
    SourceSnapshot,
)
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    FixedClock,
    InMemoryNpiLinks,
    InMemoryNpiRegistry,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    ScriptedDatasetConnector,
    dataset_capabilities,
)
from tests.synthetic import NOW, individual, make_npi, npi_person

pytestmark = pytest.mark.anyio

MONTHLY = "nppes_monthly"
NPI = make_npi("151000011")
OTHER_NPI = make_npi("151000012")
SAMPLEVILLE = Address(line1="1 Sample Way", city="Sampleville", state="NY", postal_code="99901")


class _World:
    def __init__(self, *mirror: NpiRecord, loaded: bool = True) -> None:
        self.snapshots = InMemorySnapshots()
        directory = ConnectorRegistry()
        directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.registry = InMemoryNpiRegistry(*mirror)
        self.links = InMemoryNpiLinks(InMemoryRecords(), self.registry)
        self.providers = InMemoryProviders()
        self.clock = FixedClock(NOW + timedelta(days=1))
        lookup = NpiLookup(self.registry, directory, self.snapshots, DEFAULT_FRESHNESS, self.clock)
        self.suggest = SuggestNpis(self.providers, self.links, lookup, self.clock)
        if loaded:
            self.loaded = SourceSnapshot(
                id=uuid4(),
                source_id=MONTHLY,
                fetched_at=NOW,
                outcome=Outcome.COMPLETED,
                source_url="https://example.test/nppes/monthly.zip",
                last_modified=NOW - timedelta(days=3),
                load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=2, added=2),
            )
            self.snapshots.stored.append(self.loaded)

    async def run(self, provider: Provider) -> NpiSuggestions:
        await self.providers.add(provider)
        result = await self.suggest.run(provider.organization_id, provider.id)
        assert self.providers.stored[provider.id] == provider  # nothing written to the roster
        return result


async def test_suggestions_are_scored_best_first_with_the_mirror_rows_and_provenance() -> None:
    near = npi_person(NPI, practice=SAMPLEVILLE)
    far = npi_person(OTHER_NPI)
    world = _World(far, near)
    provider = individual().model_copy(update={"addresses": (SAMPLEVILLE,)})

    result = await world.run(provider)

    assert (result.provider_id, result.computed_at, result.found) == (
        provider.id,
        NOW + timedelta(days=1),
        2,
    )
    assert [(s.npi, s.match.level) for s in result.suggestions] == [
        (NPI, MatchLevel.HIGH),
        (OTHER_NPI, MatchLevel.AMBIGUOUS),
    ]
    assert [s.record for s in result.suggestions] == [near, far]
    assert result.note is None
    assert result.mirror is not None
    assert result.mirror.provenance.snapshot_id == world.loaded.id
    assert result.mirror.provenance.source_updated_at == NOW - timedelta(days=3)
    assert result.mirror.freshness is Freshness.CACHED


async def test_the_discovery_sees_every_licence_and_address_of_the_provider() -> None:
    world = _World(npi_person(NPI, licenses=(("222", "NJ"),)))
    licences = (License(number="111", state="NY"), License(number="222", state="NJ"))
    provider = individual(licenses=licences).model_copy(update={"addresses": (SAMPLEVILLE,)})

    result = await world.run(provider)

    (party,) = world.links.parties
    assert (party.licenses, party.addresses, party.npi) == (licences, (SAMPLEVILLE,), None)
    assert [(s.npi, s.match.level) for s in result.suggestions] == [(NPI, MatchLevel.HIGH)]


async def test_a_unique_namesake_is_potential_never_exact() -> None:
    world = _World(npi_person(NPI))

    result = await world.run(individual())

    assert [(s.npi, s.match.level) for s in result.suggestions] == [(NPI, MatchLevel.POTENTIAL)]


async def test_no_candidate_is_one_none_row_saying_why() -> None:
    world = _World(npi_person(NPI, last="OTHERPERSON"))

    result = await world.run(individual())

    (suggestion,) = result.suggestions
    assert (suggestion.npi, suggestion.record, suggestion.match.level) == (
        None,
        None,
        MatchLevel.NONE,
    )
    assert suggestion.match.explanation == "No match: no candidate with this name."
    assert result.found == 0


async def test_a_provider_with_an_npi_gets_no_suggestions_and_a_note() -> None:
    world = _World(npi_person(NPI))

    result = await world.run(individual(npi=OTHER_NPI))

    assert (result.found, result.suggestions, result.note) == (0, (), HAS_NPI_NOTE)
    assert result.mirror is not None
    assert world.links.parties == []


async def test_a_mirror_never_loaded_is_one_none_row_never_an_empty_answer() -> None:
    world = _World(npi_person(NPI), loaded=False)

    result = await world.run(individual())

    assert (result.mirror, result.found) == (None, 0)
    (suggestion,) = result.suggestions
    assert (suggestion.npi, suggestion.match.level) == (None, MatchLevel.NONE)
    assert suggestion.match.explanation == MIRROR_NOT_LOADED
    assert world.links.parties == []


async def test_an_unknown_or_other_organizations_provider_is_not_found() -> None:
    world = _World(npi_person(NPI))
    provider = individual()
    await world.providers.add(provider)

    with pytest.raises(ProviderNotFoundError):
        await world.suggest.run(provider.organization_id, uuid4())
    with pytest.raises(ProviderNotFoundError):
        await world.suggest.run(uuid4(), provider.id)
    assert world.links.parties == []
