"""Confirming an NPI for a roster provider: set once, only after NPPES answered, and audited."""

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.errors import (
    ApplicationError,
    InvalidProviderInputError,
    ProviderHasNpiError,
    ProviderNotFoundError,
    RegistryUnavailableError,
)
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.set_provider_npi import SetProviderNpi
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    CredentialType,
    DatasetLoadMode,
    Finding,
    MatchLevel,
    Outcome,
)
from credentialing.domain.errors import DuplicateNpiError
from credentialing.domain.models import (
    DatasetLoad,
    NpiAnswer,
    NpiRecord,
    Provider,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.policies import DEFAULT_FRESHNESS
from credentialing.domain.types import Npi
from tests.fakes import (
    API_SOURCE,
    FakeFirstChecker,
    FixedClock,
    InMemoryNpiRegistry,
    InMemoryProviders,
    InMemorySnapshots,
    InMemoryVerifications,
    ScriptedDatasetConnector,
    ScriptedNpiRegistryConnector,
    api_failure,
    api_found,
    api_no_result,
    dataset_capabilities,
)
from tests.synthetic import NOW, individual, make_npi, npi_person, npi_stub

pytestmark = pytest.mark.anyio

MONTHLY = "nppes_monthly"
NPI = make_npi("152000011")
OTHER_NPI = make_npi("152000012")
CONFIRMED_AT = NOW + timedelta(days=1)


class _RacingProviders(InMemoryProviders):
    """Another request sets an NPI between the read and the conditional update."""

    async def set_npi(self, organization_id: UUID, provider_id: UUID, npi: Npi) -> Provider | None:
        provider = self.stored[provider_id]
        self.stored[provider_id] = provider.model_copy(update={"npi": OTHER_NPI})
        return await super().set_npi(organization_id, provider_id, npi)


class _World:
    def __init__(
        self,
        *answers: NpiAnswer,
        mirror: NpiRecord | None = None,
        loaded_at: datetime | None = NOW,
        providers: InMemoryProviders | None = None,
        recheck: FakeFirstChecker | None = None,
    ) -> None:
        self.snapshots = InMemorySnapshots()
        directory = ConnectorRegistry()
        directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.connector = ScriptedNpiRegistryConnector(*answers)
        if answers:
            directory.register(self.connector)
        self.registry = InMemoryNpiRegistry(*((mirror,) if mirror else ()))
        self.providers = providers or InMemoryProviders()
        self.verifications = InMemoryVerifications()
        clock = FixedClock(CONFIRMED_AT)
        lookup = NpiLookup(self.registry, directory, self.snapshots, DEFAULT_FRESHNESS, clock)
        verify = VerifyNpi(
            self.providers,
            lookup,
            self.verifications,
            clock,
            source_id=API_SOURCE,
            source_url="https://example.test/registry",
        )
        self.recheck = recheck or FakeFirstChecker()
        self.confirm = SetProviderNpi(self.providers, lookup, verify, clock, recheck=self.recheck)
        if loaded_at is not None:
            self.snapshots.stored.append(
                SourceSnapshot(
                    id=uuid4(),
                    source_id=MONTHLY,
                    fetched_at=loaded_at,
                    outcome=Outcome.COMPLETED,
                    source_url="https://example.test/nppes/monthly.zip",
                    load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=1, added=1),
                )
            )

    async def run(self, provider: Provider, npi: str = NPI) -> tuple[Provider, VerificationResult]:
        await self.providers.add(provider)
        updated, result = await self.confirm.run(provider.organization_id, provider.id, npi)
        assert updated == provider.model_copy(update={"npi": npi})
        assert self.providers.stored[provider.id] == updated
        assert self.verifications.stored == [result]
        assert (result.credential_type, result.provider_id) == (CredentialType.NPI, provider.id)
        assert result.details["npi_set_by"] == "user"
        assert result.details["npi_set_at"] == CONFIRMED_AT.isoformat()
        return updated, result

    async def refused(
        self, provider: Provider, expected: type[ApplicationError], npi: str = NPI
    ) -> ApplicationError:
        """The confirmation fails with ``expected``; nothing is set, nothing appended."""
        await self.providers.add(provider)
        before = self.providers.stored[provider.id]
        with pytest.raises(expected) as caught:
            await self.confirm.run(provider.organization_id, provider.id, npi)
        assert self.providers.stored[provider.id] == before
        assert self.verifications.stored == []
        assert self.recheck.calls == []
        return caught.value


async def test_an_active_npi_is_set_and_its_verification_appended() -> None:
    record = npi_person(NPI).model_copy(update={"last_updated": date(2026, 1, 10)})
    world = _World(mirror=record)

    _, result = await world.run(individual())

    assert (result.outcome, result.finding) == (Outcome.COMPLETED, Finding.RECORD_FOUND)
    assert result.summary == f"NPI {NPI} is active in NPPES (record last updated 2026-01-10)"
    assert result.details["npi_status"] == "active"
    assert result.details["name_match"] == "exact"
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT
    assert result.provenance.source_id == MONTHLY


async def test_the_audit_match_says_when_the_registry_name_differs_from_the_roster() -> None:
    world = _World(mirror=npi_person(NPI, last="OTHERPERSON"))

    _, result = await world.run(individual())

    assert result.match is not None
    assert result.match.level is MatchLevel.HIGH
    assert "name differs from the registry" in result.match.explanation


async def test_a_deactivated_npi_is_set_and_the_verification_says_so() -> None:
    stub = npi_stub(NPI).model_copy(update={"deactivation_date": date(2025, 6, 1)})
    world = _World(mirror=stub)

    _, result = await world.run(individual())

    assert result.details["npi_status"] == "deactivated"
    assert result.summary == f"NPI {NPI} is deactivated in NPPES since 2025-06-01"


async def test_an_npi_the_mirror_lacks_is_asked_live_once_and_set_when_found() -> None:
    world = _World(api_found(npi_person(NPI), fetched_at=CONFIRMED_AT))

    _, result = await world.run(individual())

    assert world.connector.lookups == [NPI]
    assert result.details["npi_status"] == "active"
    assert result.provenance.source_id == API_SOURCE


async def test_a_contradicting_live_registry_is_set_for_review_on_one_request() -> None:
    world = _World(
        api_no_result(fetched_at=CONFIRMED_AT),
        mirror=npi_person(NPI),
        loaded_at=NOW - timedelta(days=60),  # stale: the live registry is asked
    )

    _, result = await world.run(individual())

    assert world.connector.lookups == [NPI]
    assert (result.finding, result.details["npi_status"]) == (
        Finding.AMBIGUOUS,
        "mirror_only_active",
    )


async def test_an_npi_not_in_nppes_is_refused() -> None:
    world = _World(api_no_result(fetched_at=CONFIRMED_AT))

    error = await world.refused(individual(), InvalidProviderInputError)

    assert str(error) == f"NPI {NPI} is not in NPPES"


async def test_an_npi_nobody_could_check_is_never_set() -> None:
    world = _World(api_failure(Outcome.SOURCE_UNAVAILABLE, fetched_at=CONFIRMED_AT))

    error = await world.refused(individual(), RegistryUnavailableError)

    assert str(error) == "NPPES could not be checked: HTTP 503"


async def test_without_a_live_registry_or_a_mirror_load_nothing_is_set() -> None:
    world = _World(loaded_at=None)

    error = await world.refused(individual(), RegistryUnavailableError)

    assert str(error) == ("NPPES could not be checked: no live registry answer and no mirror load")


async def test_an_invalid_check_digit_is_refused_before_any_lookup() -> None:
    world = _World(api_found(npi_person(NPI), fetched_at=CONFIRMED_AT))

    wrong_digit = NPI[:9] + str((int(NPI[9]) + 1) % 10)

    await world.refused(individual(), InvalidProviderInputError, npi=wrong_digit)

    assert world.connector.lookups == []


async def test_a_provider_with_an_npi_keeps_it() -> None:
    world = _World(api_found(npi_person(NPI), fetched_at=CONFIRMED_AT))

    error = await world.refused(individual(npi=OTHER_NPI), ProviderHasNpiError)

    assert str(error) == "the provider already has an NPI; changing it is not offered"
    assert world.connector.lookups == []


async def test_an_npi_set_by_another_request_first_wins_the_race() -> None:
    world = _World(mirror=npi_person(NPI), providers=_RacingProviders())
    provider = individual()
    await world.providers.add(provider)

    with pytest.raises(ProviderHasNpiError):
        await world.confirm.run(provider.organization_id, provider.id, NPI)

    assert world.providers.stored[provider.id].npi == OTHER_NPI
    assert world.verifications.stored == []
    assert world.recheck.calls == []


async def test_an_unknown_or_other_organizations_provider_is_not_found() -> None:
    world = _World(mirror=npi_person(NPI))
    provider = individual()
    await world.providers.add(provider)

    with pytest.raises(ProviderNotFoundError):
        await world.confirm.run(uuid4(), provider.id, NPI)
    with pytest.raises(ProviderNotFoundError):
        await world.confirm.run(provider.organization_id, uuid4(), NPI)
    assert world.providers.stored[provider.id].npi is None


async def test_an_npi_another_provider_of_the_roster_holds_is_refused_before_nppes() -> None:
    world = _World(api_found(npi_person(NPI), fetched_at=CONFIRMED_AT))
    holder = individual(last="Sampleperson", npi=NPI)
    await world.providers.add(holder)
    provider = individual(organization_id=holder.organization_id)
    await world.providers.add(provider)

    with pytest.raises(DuplicateNpiError) as caught:
        await world.confirm.run(provider.organization_id, provider.id, NPI)

    assert caught.value.existing_provider_id == holder.id
    assert world.connector.lookups == []
    assert world.providers.stored[provider.id].npi is None
    assert world.verifications.stored == []


async def test_another_organizations_npi_never_blocks_the_roster() -> None:
    world = _World(mirror=npi_person(NPI))
    await world.providers.add(individual(last="Sampleperson", npi=NPI))

    await world.run(individual())


async def test_a_confirmed_npi_rechecks_the_lists_at_once_without_a_second_npi_result() -> None:
    world = _World(mirror=npi_person(NPI))
    provider = individual()

    await world.run(provider)

    assert world.recheck.calls == [(provider.organization_id, (provider.id,), "npi_set", False)]


async def test_a_recheck_that_fails_leaves_the_npi_set_and_its_verification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = _World(mirror=npi_person(NPI), recheck=FakeFirstChecker(fail=RuntimeError("outage")))
    provider = individual()

    await world.run(provider)  # asserts the NPI is set and the verification appended

    assert len(world.recheck.calls) == 1
    assert "error=RuntimeError" in caplog.text
    assert f"organization_id={provider.organization_id}" in caplog.text
    assert NPI not in caplog.text
    assert "Testperson" not in caplog.text
