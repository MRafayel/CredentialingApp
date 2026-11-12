"""The monitored roster's statuses over in-memory storage: derived, stored, listed, counted and
cleared by opening the provider, each scoped to one organization."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.list_providers import ListProviders
from credentialing.application.provider_status import (
    MarkViewed,
    ProviderStatusKeeper,
    ProviderStatusQuery,
    ProviderStatusSummary,
)
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    Attention,
    CredentialType,
    EntityKind,
    Finding,
    Freshness,
    Outcome,
    RosterOrder,
    RosterRole,
    VerificationMethod,
)
from credentialing.domain.models import Provenance, Provider, ResultChange, VerificationResult
from credentialing.domain.policies import STATUS_RULES_VERSION
from tests.fakes import FixedClock, InMemoryProviders, InMemoryVerifications
from tests.synthetic import NOW, individual, organization

pytestmark = pytest.mark.anyio

ORG_A, ORG_B = UUID(int=0xA), UUID(int=0xB)
LIST = "fake_federal"


def _result(
    provider: Provider,
    finding: Finding = Finding.NO_RECORD,
    *,
    changed: bool = False,
) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        organization_id=provider.organization_id,
        provider_id=provider.id,
        credential_type=CredentialType.EXCLUSION,
        source_id=LIST,
        outcome=Outcome.COMPLETED,
        finding=finding,
        summary="synthetic result",
        provenance=Provenance(
            source_id=LIST,
            source_url="https://example.test/list",
            method=VerificationMethod.BULK_DATASET,
            verified_at=NOW,
        ),
        freshness=Freshness.CACHED,
        created_at=NOW,
        changed_from=(
            ResultChange(
                outcome=Outcome.COMPLETED,
                finding=Finding.NO_RECORD,
                verified_at=NOW - timedelta(days=15),
            )
            if changed
            else None
        ),
    )


class _World:
    def __init__(self, *providers: Provider) -> None:
        self.providers = InMemoryProviders(*providers)
        self.verifications = InMemoryVerifications()
        self.clock = FixedClock(NOW + timedelta(hours=1))
        self.keeper = ProviderStatusKeeper(self.providers, self.verifications, ConnectorRegistry())
        self.list = ListProviders(self.providers, self.keeper)
        self.status = ProviderStatusQuery(self.providers, self.keeper)
        self.summary = ProviderStatusSummary(self.providers, self.keeper)
        self.seen = MarkViewed(self.providers, self.keeper, self.clock)


def _workforce(last: str, organization_id: UUID = ORG_A) -> Provider:
    return individual(last=last, organization_id=organization_id).model_copy(
        update={"role": RosterRole.WORKFORCE}
    )


async def test_the_roster_lists_providers_needing_attention_first_with_their_status() -> None:
    clear, listed, changed = (
        _workforce("Clearperson"),
        _workforce("Listperson"),
        _workforce("Newsperson"),
    )
    unchecked = _workforce("Laterperson")
    world = _World(clear, listed, changed, unchecked)
    await world.verifications.add_many(
        [
            _result(clear),
            _result(listed, Finding.RECORD_FOUND),
            _result(changed, changed=True),
        ]
    )

    page = await world.list.run(ORG_A, query=None, page=1, page_size=10)

    assert [item.status.attention for item in page.items] == [
        Attention.LISTED,
        Attention.CHANGED,
        Attention.NOT_CHECKED,
        None,
    ]
    assert [item.provider.id for item in page.items] == [
        listed.id,
        changed.id,
        unchecked.id,
        clear.id,
    ]
    assert page.items[0].status.listed_in == (LIST,)


async def test_the_roster_filters_by_one_attention_reason() -> None:
    clear, listed = _workforce("Clearperson"), _workforce("Listperson")
    world = _World(clear, listed)
    await world.verifications.add_many([_result(clear), _result(listed, Finding.RECORD_FOUND)])

    page = await world.list.run(ORG_A, query=None, page=1, page_size=10, attention=Attention.LISTED)

    assert [item.provider.id for item in page.items] == [listed.id]
    assert page.total == 1


async def test_the_roster_keeps_one_kind_and_takes_an_order() -> None:
    person = _workforce("Clearperson")
    group = organization("Sampleville Group").model_copy(
        update={"organization_id": ORG_A, "created_at": NOW + timedelta(minutes=1)}
    )
    world = _World(person, group)

    people = await world.list.run(
        ORG_A, query=None, page=1, page_size=10, kind=EntityKind.INDIVIDUAL
    )
    groups = await world.list.run(
        ORG_A, query=None, page=1, page_size=10, kind=EntityKind.ORGANIZATION
    )
    oldest = await world.list.run(ORG_A, query=None, page=1, page_size=10, order=RosterOrder.OLDEST)
    newest = await world.list.run(ORG_A, query=None, page=1, page_size=10, order=RosterOrder.NEWEST)

    assert [item.provider.id for item in people.items] == [person.id]
    assert [item.provider.id for item in groups.items] == [group.id]
    assert [item.provider.id for item in oldest.items] == [person.id, group.id]
    assert [item.provider.id for item in newest.items] == [group.id, person.id]


async def test_missing_statuses_are_derived_and_stored_before_a_roster_read() -> None:
    provider = _workforce("Clearperson")
    world = _World(provider)

    await world.list.run(ORG_A, query=None, page=1, page_size=10)

    assert world.providers.statuses[provider.id][2] == STATUS_RULES_VERSION


async def test_the_summary_counts_every_reason_of_one_organization() -> None:
    clear, listed, other = (
        _workforce("Clearperson"),
        _workforce("Listperson"),
        _workforce("Otherperson", ORG_B),
    )
    world = _World(clear, listed, other)
    await world.verifications.add_many([_result(clear), _result(listed, Finding.RECORD_FOUND)])

    summary = await world.summary.run(ORG_A)

    assert summary.total == 2
    assert summary.counts[Attention.LISTED] == 1
    assert summary.counts[None] == 1
    assert summary.counts[Attention.NOT_CHECKED] == 0
    assert set(summary.counts) == {*Attention, None}


async def test_opening_the_provider_clears_changed() -> None:
    provider = _workforce("Newsperson")
    world = _World(provider)
    await world.verifications.add(_result(provider, changed=True))
    before = await world.status.run(ORG_A, provider.id)

    after = await world.seen.run(ORG_A, provider.id)

    assert (before.changed, before.attention) == (True, Attention.CHANGED)
    assert (after.changed, after.attention) == (False, None)
    stored = await world.providers.get(ORG_A, provider.id)
    assert stored is not None
    assert stored.last_viewed_at == world.clock.now()
    page = await world.list.run(ORG_A, query=None, page=1, page_size=10)
    assert page.items[0].status.attention is None


async def test_another_organizations_provider_is_not_found() -> None:
    provider = _workforce("Otherperson", ORG_B)
    world = _World(provider)

    with pytest.raises(ProviderNotFoundError):
        await world.status.run(ORG_A, provider.id)
    with pytest.raises(ProviderNotFoundError):
        await world.seen.run(ORG_A, provider.id)

    stored = await world.providers.get(ORG_B, provider.id)
    assert stored is not None
    assert stored.last_viewed_at is None
    assert (await world.list.run(ORG_A, query=None, page=1, page_size=10)).total == 0


async def test_a_keeper_refuses_a_provider_of_another_organization() -> None:
    provider = _workforce("Otherperson", ORG_B)
    world = _World(provider)

    with pytest.raises(ValueError, match="organization"):
        await world.keeper.refresh_many(ORG_A, [provider])
