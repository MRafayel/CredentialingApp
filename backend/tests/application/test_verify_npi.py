"""Verifying a roster provider's NPI in NPPES: every branch is its own appended result."""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    CredentialType,
    DatasetLoadMode,
    EntityKind,
    Finding,
    Freshness,
    MatchLevel,
    NpiStatus,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    DatasetLoad,
    NpiAnswer,
    NpiRecord,
    PersonName,
    Provider,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    API_SOURCE,
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
from tests.synthetic import (
    NOW,
    individual,
    make_npi,
    npi_organization,
    npi_person,
    npi_stub,
    organization,
)

pytestmark = pytest.mark.anyio

MONTHLY = "nppes_monthly"
REGISTRY_PAGE = "https://example.test/registry"
NPI = make_npi("150000011")
REPLACEMENT = make_npi("150000012")
FILE_DATE = NOW - timedelta(days=3)


class _World:
    def __init__(self, *answers: NpiAnswer, mirror: NpiRecord | None = None) -> None:
        self.snapshots = InMemorySnapshots()
        self.directory = ConnectorRegistry()
        self.directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.connector = ScriptedNpiRegistryConnector(*answers)
        if answers:
            self.directory.register(self.connector)
        self.registry = InMemoryNpiRegistry(*((mirror,) if mirror else ()))
        self.providers = InMemoryProviders()
        self.verifications = InMemoryVerifications()
        clock = FixedClock(NOW + timedelta(days=1))
        lookup = NpiLookup(self.registry, self.directory, self.snapshots, DEFAULT_FRESHNESS, clock)
        self.verify = VerifyNpi(
            self.providers,
            lookup,
            self.verifications,
            clock,
            source_id=API_SOURCE,
            source_url=REGISTRY_PAGE,
        )
        self.loaded = SourceSnapshot(
            id=uuid4(),
            source_id=MONTHLY,
            fetched_at=NOW,
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/nppes/monthly.zip",
            last_modified=FILE_DATE,
            load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=1, added=1),
        )
        self.snapshots.stored.append(self.loaded)

    async def run(self, provider: Provider, *, live: bool = False) -> VerificationResult:
        await self.providers.add(provider)
        result = await self.verify.run(provider.organization_id, provider.id, live=live)
        assert self.verifications.stored == [result]
        assert result.credential_type is CredentialType.NPI
        assert "TESTPERSON" not in result.summary.upper()
        assert "AVERY" not in result.summary.upper()
        return result


def _provider(npi: str | None = NPI) -> Provider:
    return individual(npi=npi)


def _active(**update: object) -> NpiRecord:
    return npi_person(NPI).model_copy(update={"last_updated": date(2026, 1, 5), **update})


async def test_a_provider_without_an_npi_is_a_manual_step_and_sends_nothing() -> None:
    world = _World(api_no_result(fetched_at=NOW))

    result = await world.run(_provider(None), live=True)

    assert (result.outcome, result.finding) == (Outcome.MANUAL_REQUIRED, None)
    assert result.summary == "No NPI on file: search NPPES to find it"
    assert result.source_id == API_SOURCE
    assert result.provenance.method is VerificationMethod.OFFICIAL_API
    assert result.provenance.source_url == REGISTRY_PAGE
    assert result.freshness is Freshness.LIVE
    assert result.details == {"reason": "no_npi"}
    assert world.connector.lookups == []


async def test_an_active_npi_from_the_mirror_rests_on_the_mirror_load() -> None:
    world = _World(api_no_result(fetched_at=NOW), mirror=_active())

    result = await world.run(_provider())

    assert (result.outcome, result.finding) == (Outcome.COMPLETED, Finding.RECORD_FOUND)
    assert result.summary == f"NPI {NPI} is active in NPPES (record last updated 2026-01-05)"
    assert result.source_id == MONTHLY
    assert result.provenance.snapshot_id == world.loaded.id
    assert result.provenance.raw_record_ref == f"npi_registry/{NPI}"
    assert result.freshness is Freshness.CACHED
    assert result.details == {
        "npi_status": "active",
        "entity_kind": "individual",
        "registry_last_updated": "2026-01-05",
        "checked_live": "false",
        "name_match": "exact",
    }
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT
    assert world.connector.lookups == []


async def test_an_active_npi_checked_live_rests_on_the_live_answer() -> None:
    answer = api_found(
        _active(last_updated=date(2026, 1, 15), line_hash="api:" + "e" * 64),
        fetched_at=NOW + timedelta(days=1),
    )
    world = _World(answer, mirror=_active())

    result = await world.run(_provider(), live=True)

    assert result.finding is Finding.RECORD_FOUND
    assert result.source_id == API_SOURCE
    assert result.provenance.snapshot_id == answer.snapshot.id
    assert result.provenance.raw_record_ref == f"npi_registry/{NPI}"
    assert result.freshness is Freshness.LIVE
    assert result.details["checked_live"] == "true"
    assert result.details["registry_last_updated"] == "2026-01-15"


async def test_a_name_that_differs_from_the_registry_is_high_not_exact() -> None:
    world = _World(mirror=_active(person_name=PersonName(last="OTHERNAME", first="AVERY")))

    result = await world.run(_provider())

    assert result.match is not None
    assert result.match.level is MatchLevel.HIGH
    assert result.details["name_match"] == "high"
    assert result.finding is Finding.RECORD_FOUND


async def test_a_deactivated_npi_names_its_date_and_replacement() -> None:
    stub = npi_stub(NPI).model_copy(
        update={"deactivation_date": date(2024, 6, 30), "replacement_npi": REPLACEMENT}
    )
    world = _World(api_no_result(fetched_at=NOW + timedelta(days=1)), mirror=stub)

    result = await world.run(_provider(), live=True)

    assert (result.outcome, result.finding) == (Outcome.COMPLETED, Finding.RECORD_FOUND)
    assert result.summary == (
        f"NPI {NPI} is deactivated in NPPES since 2024-06-30, replaced by {REPLACEMENT}"
    )
    assert result.details["npi_status"] == "deactivated"
    assert result.details["checked_live"] == "true"
    assert result.source_id == MONTHLY
    assert result.match is None


async def test_a_deactivated_npi_with_a_name_is_still_matched() -> None:
    record = _active(status=NpiStatus.DEACTIVATED, deactivation_date=date(2024, 6, 30))
    world = _World(mirror=record)

    result = await world.run(_provider())

    assert result.summary == f"NPI {NPI} is deactivated in NPPES since 2024-06-30"
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT
    assert "name_match" not in result.details


async def test_an_active_mirror_the_live_registry_does_not_list_goes_to_review() -> None:
    answer = api_no_result(fetched_at=NOW + timedelta(days=1))
    world = _World(answer, mirror=_active())

    result = await world.run(_provider(), live=True)

    assert (result.outcome, result.finding) == (Outcome.COMPLETED, Finding.AMBIGUOUS)
    assert result.summary == (
        f"The NPPES file of {FILE_DATE.date().isoformat()} lists NPI {NPI} as active, "
        "but the live registry returned no result: review"
    )
    assert result.details["npi_status"] == "mirror_only_active"
    assert result.source_id == API_SOURCE
    assert result.provenance.snapshot_id == answer.snapshot.id


async def test_an_npi_neither_mirrored_nor_listed_live_is_not_in_nppes() -> None:
    answer = api_no_result(fetched_at=NOW + timedelta(days=1))
    world = _World(answer)

    result = await world.run(_provider())

    assert (result.outcome, result.finding) == (Outcome.COMPLETED, Finding.NO_RECORD)
    assert result.summary == f"NPI {NPI} is not in NPPES (checked live 2026-01-16 12:00 UTC)"
    assert result.details == {"npi_status": "not_found", "checked_live": "true"}
    assert result.source_id == API_SOURCE
    assert result.provenance.raw_record_ref == f"source_snapshots/{answer.snapshot.id}"
    assert result.match is None


@pytest.mark.parametrize("outcome", [Outcome.SOURCE_UNAVAILABLE, Outcome.BLOCKED, Outcome.FAILED])
async def test_a_failed_request_without_a_mirror_row_could_not_check(outcome: Outcome) -> None:
    world = _World(api_failure(outcome, fetched_at=NOW + timedelta(days=1), note="HTTP 503"))

    result = await world.run(_provider())

    assert (result.outcome, result.finding) == (outcome, None)
    assert result.summary == "NPPES could not be checked: HTTP 503"
    assert result.details == {"live_check_outcome": outcome.value}
    assert result.source_id == API_SOURCE


async def test_a_failed_request_with_a_mirror_row_answers_from_the_mirror_and_says_so() -> None:
    world = _World(
        api_failure(Outcome.SOURCE_UNAVAILABLE, fetched_at=NOW + timedelta(days=1)),
        mirror=_active(),
    )

    result = await world.run(_provider(), live=True)

    assert result.finding is Finding.RECORD_FOUND
    assert result.source_id == MONTHLY
    assert result.freshness is Freshness.CACHED
    assert result.details["checked_live"] == "false"
    assert result.details["live_check_outcome"] == "source_unavailable"
    assert result.details["live_check_at"] == (NOW + timedelta(days=1)).isoformat()


async def test_without_a_live_registry_or_a_mirror_row_nothing_could_be_checked() -> None:
    world = _World()

    result = await world.run(_provider())

    assert (result.outcome, result.finding) == (Outcome.SOURCE_UNAVAILABLE, None)
    assert result.details == {"reason": "no_registry_answer"}


async def test_an_organization_is_matched_by_its_name() -> None:
    record = npi_organization(NPI).model_copy(update={"last_updated": date(2026, 1, 5)})
    world = _World(mirror=record)

    result = await world.run(organization("Sampleville Care Group", npi=NPI))

    assert result.details["entity_kind"] == EntityKind.ORGANIZATION.value
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT


async def test_an_unknown_provider_is_not_found() -> None:
    world = _World()

    with pytest.raises(ProviderNotFoundError):
        await world.verify.run(UUID(int=1), uuid4())
