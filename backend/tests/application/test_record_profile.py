from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.application.errors import RecordNotFoundError
from credentialing.application.record_profile import (
    MAX_SAME_NAME,
    SAME_NAME_CANDIDATES,
    RecordProfileQuery,
)
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import EntityKind, Freshness, Outcome, RecordLink, RecordStatus
from credentialing.domain.models import ExclusionRecord, License, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    FixedClock,
    InMemoryRecords,
    InMemorySnapshots,
    ScriptedConnector,
    capabilities,
)
from tests.synthetic import NOW, exclusion, make_npi

pytestmark = pytest.mark.anyio

STATE = "fake_wa_list"
FEDERAL = "fake_federal"
BUSINESS_NPI = make_npi("100000001")
OWNER_NPI = make_npi("100000002")
LICENCE = License(number="DN 60000001", state="WA")


class _World:
    def __init__(self) -> None:
        registry = ConnectorRegistry()
        registry.register(
            ScriptedConnector(
                capabilities(STATE, frozenset({"WA"}), display_name="Fake Washington List")
            )
        )
        registry.register(ScriptedConnector(capabilities(FEDERAL, display_name="Fake Federal")))
        self.records = InMemoryRecords()
        self.snapshots = InMemorySnapshots()
        self.use_case = RecordProfileQuery(
            self.records, self.snapshots, registry, DEFAULT_FRESHNESS, FixedClock(NOW)
        )

    async def load(self, source_id: str, *records: ExclusionRecord) -> None:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=source_id,
            fetched_at=max(r.valid_from for r in records),
            outcome=Outcome.COMPLETED,
            source_url="https://example.test/list",
        )
        await self.snapshots.add(snapshot)
        own = [r.model_copy(update={"snapshot_id": snapshot.id}) for r in records]
        await self.records.replace_current(source_id, snapshot.id, own, snapshot.fetched_at)


def _business(**changes: object) -> ExclusionRecord:
    fields: dict[str, object] = {
        "source_id": STATE,
        "entity_kind": EntityKind.ORGANIZATION,
        "last": None,
        "organization_name": "Sample Dentures NW",
        "related_parties": ("Exampleowner, Jordan L. DN",),
        "npi": BUSINESS_NPI,
        "related_npis": (OWNER_NPI,),
        "license": LICENCE,
        "raw": {"Name": "Sample Dentures NW\nExampleowner, Jordan L. DN"},
        **changes,
    }
    return exclusion(**fields)  # type: ignore[arg-type]  # fields of mixed types


async def test_a_record_with_everything_around_it() -> None:
    world = _World()
    first = _business(valid_from=NOW - timedelta(days=30))
    current = _business(end_date=date(2025, 1, 1))
    await world.load(STATE, first)
    await world.load(STATE, current)
    owner = exclusion(source_id=FEDERAL, last="Exampleowner", first="Jordan", npi=OWNER_NPI)
    same_npi = _business(source_id=FEDERAL, related_parties=(), related_npis=())
    same_licence = exclusion(source_id=FEDERAL, last="Licenceholder", license=LICENCE)
    naming = exclusion(
        source_id=FEDERAL,
        last="Otherperson",
        npi=make_npi("100000003"),
        related_npis=(BUSINESS_NPI,),
    )
    same_name = exclusion(
        source_id=FEDERAL,
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="SAMPLE DENTURES NW LLC",
    )
    unrelated = exclusion(source_id=FEDERAL, last="Unrelated")
    await world.load(FEDERAL, owner, same_npi, same_licence, naming, same_name, unrelated)

    profile = await world.use_case.run(current.id)

    assert profile.record.record.id == current.id
    assert profile.record.source_name == "Fake Washington List"
    assert profile.record.status is RecordStatus.ENDED
    assert profile.source.display_name == "Fake Washington List"
    assert profile.source.source_url == f"https://example.test/{STATE}"
    assert profile.source.fetched_at == NOW
    assert profile.source.freshness is Freshness.LIVE
    assert profile.first_seen == NOW - timedelta(days=30)
    assert [v.end_date for v in profile.versions] == [None, date(2025, 1, 1)]
    assert [(r.record.id, r.link, r.via_npi) for r in profile.same_party] == [
        (same_npi.id, RecordLink.BY_NPI, BUSINESS_NPI),
        (same_licence.id, RecordLink.BY_LICENSE, None),
    ]
    assert {(r.record.id, r.link, r.via_npi) for r in profile.related} == {
        (owner.id, RecordLink.LISTED_AS, OWNER_NPI),
        (naming.id, RecordLink.NAMED_ALONGSIDE, BUSINESS_NPI),
    }
    assert [(r.record.id, r.link) for r in profile.same_name] == [
        (same_name.id, RecordLink.SAME_NAME)
    ]
    assert all(r.source_name == "Fake Federal" for r in profile.related)
    assert all(r.status is RecordStatus.ACTIVE for r in profile.related)


async def test_a_record_alone_has_only_itself() -> None:
    world = _World()
    alone = exclusion(source_id=FEDERAL, npi=make_npi("100000004"))
    await world.load(FEDERAL, alone)

    profile = await world.use_case.run(alone.id)

    assert profile.record.status is RecordStatus.ACTIVE
    assert profile.first_seen == alone.valid_from
    assert len(profile.versions) == 1
    assert profile.same_party == profile.related == profile.same_name == ()


async def test_same_name_records_are_capped() -> None:
    world = _World()
    subject = exclusion(source_id=STATE)
    namesakes = [
        exclusion(source_id=FEDERAL, effective_date=date(2020, 1, 1) + timedelta(days=day))
        for day in range(MAX_SAME_NAME + 3)
    ]
    await world.load(STATE, subject)
    await world.load(FEDERAL, *namesakes)

    profile = await world.use_case.run(subject.id)

    assert len(profile.same_name) == MAX_SAME_NAME
    assert subject.id not in {r.record.id for r in profile.same_name}


async def test_an_unregistered_source_is_named_by_its_id() -> None:
    world = _World()
    orphan = exclusion(source_id="retired_list")
    await world.load("retired_list", orphan)

    profile = await world.use_case.run(orphan.id)

    assert profile.source.display_name == "retired_list"
    assert profile.source.source_url is None
    assert profile.source.freshness is None


async def test_an_unknown_record_is_not_found() -> None:
    with pytest.raises(RecordNotFoundError):
        await _World().use_case.run(uuid4())


async def test_namesakes_beyond_the_candidate_bound_are_found_first_name_first() -> None:
    world = _World()
    subject = exclusion(source_id=STATE, last="Commonname", first="Avery")
    surname_only = [
        exclusion(
            source_id=FEDERAL,
            last="Commonname",
            first="Blake",
            effective_date=date(2020, 1, 1) + timedelta(days=day),
        )
        for day in range(SAME_NAME_CANDIDATES + 5)
    ]
    namesake = exclusion(source_id="zz_last_list", last="Commonname", first="AVERY")
    await world.load(STATE, subject)
    await world.load(FEDERAL, *surname_only)
    await world.load("zz_last_list", namesake)

    profile = await world.use_case.run(subject.id)

    assert [r.record.id for r in profile.same_name] == [namesake.id]


async def test_a_licence_in_another_state_is_not_the_same_party() -> None:
    world = _World()
    subject = exclusion(source_id=STATE, last="Holder", license=LICENCE)
    elsewhere = exclusion(
        source_id=FEDERAL, last="Elsewhere", license=License(number="DN60000001", state="OR")
    )
    await world.load(STATE, subject)
    await world.load(FEDERAL, elsewhere)

    profile = await world.use_case.run(subject.id)

    assert profile.same_party == ()
