from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.errors import ProviderNotFoundError
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    MatchLevel,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    ExclusionRecord,
    License,
    Provider,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    FixedClock,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
    ScriptedConnector,
    capabilities,
)
from tests.synthetic import NOW, exclusion, individual, make_npi

pytestmark = pytest.mark.anyio

FEDERAL = "fake_federal"
STATE = "fake_ny_list"
LIST_UPDATED = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
NPI = make_npi("100000001")


class _World:
    """A federal and a New York list, in-memory storage, and one provider."""

    def __init__(self, provider: Provider) -> None:
        self.registry = ConnectorRegistry()
        self.registry.register(ScriptedConnector(capabilities(FEDERAL)))
        self.registry.register(
            ScriptedConnector(
                capabilities(STATE, frozenset({"NY"}), display_name="Fake New York List")
            )
        )
        self.snapshots = InMemorySnapshots()
        self.records = InMemoryRecords()
        self.verifications = InMemoryVerifications()
        self.provider = provider
        self.use_case = CheckExclusions(
            self.registry,
            InMemoryProviders(provider),
            self.records,
            self.snapshots,
            self.verifications,
            DEFAULT_FRESHNESS,
            FixedClock(NOW),
        )

    async def load(
        self,
        source_id: str,
        *records: ExclusionRecord,
        age: timedelta = timedelta(hours=3),
        outcome: Outcome = Outcome.COMPLETED,
    ) -> SourceSnapshot:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=source_id,
            fetched_at=NOW - age,
            outcome=outcome,
            source_url="https://example.test/list",
            last_modified=LIST_UPDATED if outcome is Outcome.COMPLETED else None,
        )
        await self.snapshots.add(snapshot)
        if outcome is Outcome.COMPLETED:
            own = tuple(r.model_copy(update={"snapshot_id": snapshot.id}) for r in records)
            await self.records.replace_current(source_id, snapshot.id, own, snapshot.fetched_at)
        return snapshot

    async def check(self) -> dict[str, VerificationResult]:
        results = await self.use_case.run(self.provider.organization_id, self.provider.id)
        return {result.source_id: result for result in results}


def _ny_provider(npi: str | None = None) -> Provider:
    return individual(licenses=(License(number="123456", state="NY"),), npi=npi)


async def test_a_provider_is_checked_against_federal_and_state_lists() -> None:
    world = _World(_ny_provider())
    federal = await world.load(FEDERAL, exclusion(source_id=FEDERAL, last="Otherperson"))
    await world.load(STATE, exclusion(source_id=STATE, last="Otherperson"))

    results = await world.check()

    assert list(results) == [FEDERAL, STATE]
    result = results[FEDERAL]
    assert result.outcome is Outcome.COMPLETED
    assert result.finding is Finding.NO_RECORD
    assert result.match is None
    assert result.matched_record_ids == ()
    assert result.summary == "No record in Fake Federal List (list fetched 2026-01-15 09:00 UTC)"
    assert result.credential_type is CredentialType.EXCLUSION
    assert result.provenance.source_url == "https://example.test/fake_federal"
    assert result.provenance.method is VerificationMethod.BULK_DATASET
    assert result.provenance.verified_at == NOW
    assert result.provenance.source_updated_at == LIST_UPDATED
    assert result.provenance.snapshot_id == federal.id
    assert result.freshness is Freshness.CACHED
    assert world.verifications.stored == [results[FEDERAL], results[STATE]]


async def test_without_a_state_only_federal_lists_apply() -> None:
    world = _World(individual())
    await world.load(FEDERAL)

    assert list(await world.check()) == [FEDERAL]


async def test_an_npi_match_is_a_record_found() -> None:
    world = _World(_ny_provider(npi=NPI))
    listed = exclusion(
        source_id=FEDERAL, last="Testperson", npi=NPI, effective_date=date(2014, 3, 1)
    )
    await world.load(FEDERAL, listed)
    await world.load(STATE)

    result = (await world.check())[FEDERAL]

    assert result.finding is Finding.RECORD_FOUND
    assert result.match is not None
    assert result.match.level is MatchLevel.EXACT
    assert result.matched_record_ids == (listed.id,)
    assert result.summary == ("Listed in Fake Federal List: exclusion effective 2014-03-01")
    assert result.provenance.raw_record_ref == f"exclusion_records/{listed.id}"


async def test_a_name_only_match_needs_review() -> None:
    world = _World(_ny_provider())
    same_name = exclusion(source_id=STATE, last="Testperson", first="Avery")
    initial_only = exclusion(source_id=STATE, last="Testperson", first="A")
    await world.load(FEDERAL)
    await world.load(STATE, same_name, initial_only)

    result = (await world.check())[STATE]

    assert result.finding is Finding.AMBIGUOUS
    assert result.match is not None
    assert result.match.level is MatchLevel.POTENTIAL
    assert result.matched_record_ids == (same_name.id,)
    assert result.summary == "Possible match in Fake New York List: review required"


async def test_a_list_never_loaded_cannot_be_checked() -> None:
    world = _World(_ny_provider())
    await world.load(FEDERAL)
    blocked = await world.load(STATE, outcome=Outcome.BLOCKED)

    result = (await world.check())[STATE]

    assert result.outcome is Outcome.SOURCE_UNAVAILABLE
    assert result.finding is None
    assert result.match is None
    assert "never been loaded" in result.summary
    assert result.provenance.snapshot_id == blocked.id
    assert result.provenance.source_updated_at is None
    assert result.details["latest_fetch_outcome"] == "blocked"


async def test_an_old_list_is_stale_and_a_failed_latest_fetch_is_reported() -> None:
    world = _World(individual())
    loaded = await world.load(FEDERAL, age=timedelta(days=10))
    await world.load(FEDERAL, age=timedelta(hours=1), outcome=Outcome.SOURCE_UNAVAILABLE)

    result = (await world.check())[FEDERAL]

    assert result.finding is Finding.NO_RECORD
    assert result.freshness is Freshness.STALE
    assert result.provenance.snapshot_id == loaded.id
    assert result.details["latest_fetch_outcome"] == "source_unavailable"
    assert result.details["list_fetched_at"] == loaded.fetched_at.isoformat()


async def test_an_unknown_provider() -> None:
    world = _World(individual())

    with pytest.raises(ProviderNotFoundError):
        await world.use_case.run(world.provider.organization_id, uuid4())


async def test_another_organizations_provider_is_not_found() -> None:
    world = _World(individual())

    with pytest.raises(ProviderNotFoundError):
        await world.use_case.run(UUID(int=7), world.provider.id)
