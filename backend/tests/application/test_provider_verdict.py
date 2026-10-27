from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.application.errors import ProviderNotFoundError
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    Outcome,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import Provenance, Provider, VerificationResult
from tests.fakes import InMemoryProviders, InMemoryVerifications
from tests.synthetic import NOW, individual

pytestmark = pytest.mark.anyio


def _result(
    provider: Provider,
    source_id: str,
    finding: Finding,
    *,
    hours_later: int = 0,
    credential_type: CredentialType = CredentialType.EXCLUSION,
) -> VerificationResult:
    at = NOW + timedelta(hours=hours_later)
    return VerificationResult(
        id=uuid4(),
        organization_id=provider.organization_id,
        provider_id=provider.id,
        credential_type=credential_type,
        source_id=source_id,
        outcome=Outcome.COMPLETED,
        finding=finding,
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


async def test_the_verdict_reads_the_latest_result_of_each_source() -> None:
    provider = individual()
    verifications = InMemoryVerifications()
    found = _result(provider, "list_a", Finding.RECORD_FOUND)
    cleared = _result(provider, "list_a", Finding.NO_RECORD, hours_later=1)
    review = _result(provider, "list_b", Finding.AMBIGUOUS)
    licence = _result(
        provider, "list_c", Finding.RECORD_FOUND, credential_type=CredentialType.STATE_LICENSE
    )
    for result in (found, cleared, review, licence):
        await verifications.add(result)
    query = ProviderVerdictQuery(InMemoryProviders(provider), verifications)

    verdict = await query.run(provider.organization_id, provider.id)

    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.results == (cleared, review)
    assert verdict.review_in == ("list_b",)
    assert verdict.listed_in == ()


async def test_a_provider_without_results_is_not_checked() -> None:
    provider = individual()
    query = ProviderVerdictQuery(InMemoryProviders(provider), InMemoryVerifications())

    verdict = await query.run(provider.organization_id, provider.id)

    assert verdict.verdict is Verdict.NOT_CHECKED


async def test_another_organizations_provider_is_not_found() -> None:
    provider = individual()
    query = ProviderVerdictQuery(InMemoryProviders(provider), InMemoryVerifications())

    with pytest.raises(ProviderNotFoundError):
        await query.run(uuid4(), provider.id)
