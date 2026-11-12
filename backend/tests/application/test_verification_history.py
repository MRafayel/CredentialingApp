"""Reading a provider's history a page of moments at a time, over in-memory storage."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.verification_history import VerificationHistory
from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import Provenance, Provider, VerificationResult
from credentialing.domain.policies import HISTORY_RETENTION
from tests.fakes import InMemoryProviders, InMemoryRecords, InMemoryVerifications
from tests.synthetic import NOW, individual

pytestmark = pytest.mark.anyio


def _result(
    provider: Provider,
    at: datetime,
    source_id: str = "fake_federal",
    credential_type: CredentialType = CredentialType.EXCLUSION,
) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        organization_id=provider.organization_id,
        provider_id=provider.id,
        credential_type=credential_type,
        source_id=source_id,
        outcome=Outcome.COMPLETED,
        finding=Finding.NO_RECORD,
        summary="synthetic result",
        provenance=Provenance(
            source_id=source_id,
            source_url=f"https://example.test/{source_id}",
            method=VerificationMethod.BULK_DATASET,
            verified_at=at,
        ),
        freshness=Freshness.CACHED,
        created_at=at,
    )


def _world(provider: Provider) -> tuple[VerificationHistory, InMemoryVerifications]:
    verifications = InMemoryVerifications()
    history = VerificationHistory(InMemoryProviders(provider), verifications, InMemoryRecords())
    return history, verifications


async def test_pages_follow_one_another_by_moments_until_none_is_left() -> None:
    provider = individual()
    history, verifications = _world(provider)
    runs = [
        [_result(provider, NOW + timedelta(days=day), s) for s in ("fake_federal", "fake_state")]
        for day in (0, 15, 30)
    ]
    npi = _result(provider, NOW + timedelta(days=31), "fake_npi", CredentialType.NPI)
    for run in runs:
        await verifications.add_many(run)
    await verifications.add(npi)

    first = await history.page(provider.organization_id, provider.id, before=None, moments=2)
    second = await history.page(
        provider.organization_id, provider.id, before=first.next_before, moments=2
    )

    assert [view.result for view in first.views] == [npi, *runs[2]]
    assert first.next_before == NOW + timedelta(days=30)
    assert [view.result for view in second.views] == [*runs[1], *runs[0]]
    assert second.next_before is None
    assert first.retention == HISTORY_RETENTION


async def test_the_latest_npi_verification_may_be_older_than_the_first_page() -> None:
    provider = individual()
    history, verifications = _world(provider)
    assert await history.latest_npi(provider.organization_id, provider.id) is None
    npi = _result(provider, NOW, "fake_npi", CredentialType.NPI)
    await verifications.add(npi)
    for day in range(1, 12):
        await verifications.add(_result(provider, NOW + timedelta(days=day)))

    page = await history.page(provider.organization_id, provider.id, before=None, moments=10)
    latest = await history.latest_npi(provider.organization_id, provider.id)

    assert npi not in [view.result for view in page.views]
    assert latest is not None
    assert latest.result == npi


async def test_another_organizations_provider_is_not_found() -> None:
    provider = individual()
    history, _ = _world(provider)

    with pytest.raises(ProviderNotFoundError):
        await history.page(uuid4(), provider.id, before=None, moments=10)
    with pytest.raises(ProviderNotFoundError):
        await history.latest_npi(uuid4(), provider.id)
