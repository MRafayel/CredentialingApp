from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.errors import ProviderNotFoundError
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    ExclusionAction,
    Finding,
    Freshness,
    MatchLevel,
    MatchRelation,
    Outcome,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    Provider,
    ProviderVerdict,
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
from tests.synthetic import NOW, exclusion, individual, make_npi, organization

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

    async def verdict(self) -> ProviderVerdict:
        return await self.use_case.run(self.provider.organization_id, self.provider.id)

    async def check(self) -> dict[str, VerificationResult]:
        return {result.source_id: result for result in (await self.verdict()).results}


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


async def test_the_run_returns_the_verdict_of_its_own_results() -> None:
    world = _World(_ny_provider(npi=NPI))
    await world.load(FEDERAL, exclusion(source_id=FEDERAL, last="Testperson", npi=NPI))
    await world.load(STATE)
    earlier = await world.verdict()
    await world.load(FEDERAL, age=timedelta(hours=1))

    verdict = await world.verdict()

    assert earlier.verdict is Verdict.LISTED
    assert earlier.listed_in == (FEDERAL,)
    # The federal list no longer names them: the earlier finding does not linger.
    assert verdict.verdict is Verdict.NO_RECORDS
    assert verdict.sources_checked == 2
    assert verdict.listed_in == ()
    assert verdict.last_checked_at == NOW
    assert verdict.oldest_list_updated_at == LIST_UPDATED
    assert [r.source_id for r in verdict.results] == [FEDERAL, STATE]
    assert list(verdict.results) == world.verifications.stored[-2:]


async def test_without_a_covering_list_the_provider_is_not_checked() -> None:
    world = _World(individual())
    world.use_case = CheckExclusions(
        ConnectorRegistry(),
        InMemoryProviders(world.provider),
        world.records,
        world.snapshots,
        world.verifications,
        DEFAULT_FRESHNESS,
        FixedClock(NOW),
    )

    verdict = await world.verdict()

    assert verdict.verdict is Verdict.NOT_CHECKED
    assert verdict.results == ()
    assert world.verifications.stored == []


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


async def test_an_organization_listed_with_a_legal_suffix_needs_review() -> None:
    provider = organization("7 Star Sampleville Pharmacy and Deli").model_copy(
        update={"addresses": (Address(city="Sampleville", state="NY"),)}
    )
    world = _World(provider)
    listed = exclusion(
        source_id=STATE,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="7 STAR SAMPLEVILLE PHARMACY AND DELI INC",
    )
    await world.load(FEDERAL)
    await world.load(STATE, listed)

    result = (await world.check())[STATE]

    assert result.finding is Finding.AMBIGUOUS
    assert result.match is not None
    assert result.match.level is MatchLevel.POTENTIAL
    assert result.match.evidence[0].detail == "legal suffix ignored"
    assert result.matched_record_ids == (listed.id,)


async def test_records_with_the_providers_licence_number_are_compared() -> None:
    world = _World(_ny_provider())
    same_licence = exclusion(
        source_id=STATE, last="Othername", license=License(number="12 3456", state="NY")
    )
    await world.load(FEDERAL)
    await world.load(STATE, same_licence)

    result = (await world.check())[STATE]

    # Found as a candidate by licence; a licence alone is never a finding, so a person looks.
    assert result.details["candidates_compared"] == "1"
    assert result.finding is Finding.AMBIGUOUS
    assert result.match is not None
    assert result.match.level is MatchLevel.AMBIGUOUS
    assert result.matched_record_ids == (same_licence.id,)
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


async def test_a_found_record_that_has_ended_is_previously_listed() -> None:
    world = _World(_ny_provider(npi=NPI))
    ended = exclusion(
        source_id=STATE,
        npi=NPI,
        effective_date=date(2019, 1, 1),
        end_date=date(2024, 1, 1),
    )
    await world.load(FEDERAL)
    await world.load(STATE, ended)

    verdict = await world.verdict()
    result = {r.source_id: r for r in verdict.results}[STATE]

    assert result.finding is Finding.RECORD_FOUND
    assert result.details["record_status"] == "ended"
    assert result.summary == (
        "Previously listed in Fake New York List: exclusion effective 2019-01-01 "
        "\u2013 ended 2024-01-01"
    )
    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.listed_in == ()
    assert verdict.review_in == (STATE,)


async def test_a_record_ending_in_the_future_still_stands() -> None:
    world = _World(_ny_provider(npi=NPI))
    standing = exclusion(
        source_id=STATE, npi=NPI, effective_date=date(2025, 6, 1), end_date=date(2027, 6, 1)
    )
    await world.load(FEDERAL)
    await world.load(STATE, standing)

    verdict = await world.verdict()
    result = {r.source_id: r for r in verdict.results}[STATE]

    assert result.details["record_status"] == "active"
    assert result.summary == (
        "Listed in Fake New York List: exclusion effective 2025-06-01 \u2013 ends 2027-06-01"
    )
    assert verdict.verdict is Verdict.LISTED


async def test_one_standing_record_among_ended_ones_keeps_the_provider_listed() -> None:
    world = _World(_ny_provider(npi=NPI))
    ended = exclusion(
        source_id=STATE, npi=NPI, effective_date=date(2015, 1, 1), end_date=date(2016, 1, 1)
    )
    standing = exclusion(
        source_id=STATE, npi=NPI, effective_date=date(2020, 3, 1), action=ExclusionAction.SUSPENSION
    )
    reinstated = exclusion(
        source_id=STATE,
        npi=NPI,
        effective_date=date(2016, 1, 1),
        action=ExclusionAction.REINSTATEMENT,
    )
    await world.load(FEDERAL)
    await world.load(STATE, ended, standing, reinstated)

    verdict = await world.verdict()
    result = {r.source_id: r for r in verdict.results}[STATE]

    assert result.details["record_status"] == "active"
    assert result.summary == (
        "Listed in Fake New York List: suspension effective 2020-03-01 (3 matching records)"
    )
    assert verdict.verdict is Verdict.LISTED


async def test_a_reinstatement_alone_is_previously_listed() -> None:
    world = _World(_ny_provider(npi=NPI))
    reinstated = exclusion(
        source_id=STATE,
        npi=NPI,
        effective_date=date(2016, 1, 1),
        action=ExclusionAction.REINSTATEMENT,
    )
    await world.load(FEDERAL)
    await world.load(STATE, reinstated)

    verdict = await world.verdict()
    result = {r.source_id: r for r in verdict.results}[STATE]

    assert result.details["record_status"] == "ended"
    assert result.summary == (
        "Previously listed in Fake New York List: reinstatement effective 2016-01-01"
    )
    assert verdict.verdict is Verdict.NEEDS_REVIEW


async def test_a_check_without_a_matched_record_has_no_record_status() -> None:
    world = _World(_ny_provider())
    await world.load(FEDERAL)
    await world.load(STATE)

    results = await world.check()

    assert "record_status" not in results[STATE].details


BUSINESS_NPI = make_npi("100000003")


def _business_naming_the_provider(**changes: object) -> ExclusionRecord:
    """A business row that names the provider as its owner and publishes their NPI."""
    fields: dict[str, object] = {
        "source_id": STATE,
        "entity_kind": EntityKind.ORGANIZATION,
        "last": None,
        "organization_name": "Sample Dentures NW",
        "related_parties": ("Testperson, Avery",),
        "npi": BUSINESS_NPI,
        "related_npis": (NPI,),
        "effective_date": date(2021, 4, 1),
        **changes,
    }
    return exclusion(**fields)  # type: ignore[arg-type]  # fields of mixed types


async def test_a_provider_named_as_a_related_party_is_flagged_not_listed() -> None:
    world = _World(_ny_provider(npi=NPI))
    business = _business_naming_the_provider()
    await world.load(FEDERAL)
    await world.load(STATE, business)

    verdict = await world.verdict()
    result = {r.source_id: r for r in verdict.results}[STATE]

    assert result.finding is Finding.RELATED_PARTY
    assert result.match is not None
    assert result.match.level is MatchLevel.HIGH
    assert result.match.relation is MatchRelation.RELATED_PARTY
    assert result.matched_record_ids == (business.id,)
    assert result.summary == (
        "Flagged in Fake New York List: named with a listed party, exclusion effective 2021-04-01"
    )
    assert verdict.verdict is Verdict.FLAGGED
    assert verdict.flagged_in == (STATE,)
    assert verdict.listed_in == verdict.review_in == ()


async def test_an_ended_related_party_record_is_previously_flagged() -> None:
    world = _World(_ny_provider(npi=NPI))
    await world.load(FEDERAL)
    await world.load(STATE, _business_naming_the_provider(end_date=date(2024, 1, 1)))

    verdict = await world.verdict()
    result = {r.source_id: r for r in verdict.results}[STATE]

    assert result.finding is Finding.RELATED_PARTY
    assert result.summary == (
        "Previously flagged in Fake New York List: named with a listed party, exclusion "
        "effective 2021-04-01 \u2013 ended 2024-01-01"
    )
    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.flagged_in == ()
    assert verdict.review_in == (STATE,)


async def test_a_party_match_outranks_a_related_party_match_at_the_same_level() -> None:
    world = _World(_ny_provider(npi=NPI))
    related = _business_naming_the_provider().model_copy(update={"id": UUID(int=1)})
    party = exclusion(source_id=STATE, license=License(number="123456", state="NY")).model_copy(
        update={"id": UUID(int=2)}
    )
    await world.load(FEDERAL)
    await world.load(STATE, related, party)

    result = (await world.check())[STATE]

    assert result.finding is Finding.RECORD_FOUND
    assert result.match is not None
    assert result.match.level is MatchLevel.HIGH
    assert result.match.relation is MatchRelation.PARTY
    assert result.matched_record_ids == (party.id, related.id)
    assert result.summary.startswith("Listed in Fake New York List: ")
    assert result.summary.endswith("(2 matching records)")
