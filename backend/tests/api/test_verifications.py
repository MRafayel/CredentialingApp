"""The NPI verification route over in-memory use cases: fast, no database."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import httpx2
import pytest

from credentialing.api.container import AppContainer
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.verification_history import VerificationHistory
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import NpiAnswer, Provenance, VerificationResult
from credentialing.domain.policies import DEFAULT_FRESHNESS, HISTORY_RETENTION
from tests.api.test_providers import HEADERS, ORGANIZATION, _client, _container
from tests.fakes import (
    API_SOURCE,
    FixedClock,
    InMemoryNpiRegistry,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
    ScriptedNpiRegistryConnector,
    api_found,
    api_no_result,
)
from tests.synthetic import NOW, make_npi, npi_person

pytestmark = pytest.mark.anyio

NPI = make_npi("180000011")


def _world(*answers: NpiAnswer) -> tuple[AppContainer, ScriptedNpiRegistryConnector]:
    base = _container()
    directory = ConnectorRegistry()
    live = ScriptedNpiRegistryConnector(*answers)
    directory.register(live)
    clock = FixedClock(NOW)
    lookup = NpiLookup(
        InMemoryNpiRegistry(), directory, InMemorySnapshots(), DEFAULT_FRESHNESS, clock
    )
    verifications = InMemoryVerifications()
    container = replace(
        base,
        npi_lookup=lookup,
        verification_history=VerificationHistory(base.providers, verifications, InMemoryRecords()),
        verify_npi=VerifyNpi(
            base.providers,
            lookup,
            verifications,
            clock,
            source_id=API_SOURCE,
            source_url="https://example.test/registry",
        ),
    )
    return container, live


async def _register(client: httpx2.AsyncClient, npi: str | None) -> str:
    body = {"entity_kind": "individual", "person_name": {"last": "Testperson", "first": "Avery"}}
    if npi:
        body["npi"] = npi
    created = await client.post("/providers", json=body, headers=HEADERS)
    assert created.status_code == 201
    return str(created.json()["id"])


async def test_an_npi_verification_is_appended_and_shown_in_the_history() -> None:
    record = npi_person(NPI).model_copy(update={"last_updated": date(2026, 1, 10)})
    container, live = _world(api_found(record, fetched_at=NOW))

    async for client in _client(container):
        provider_id = await _register(client, NPI)
        response = await client.post(
            f"/providers/{provider_id}/verifications/npi?live=true", headers=HEADERS
        )
        history = await client.get(f"/providers/{provider_id}/verifications", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["credential_type"] == "npi"
    assert (body["outcome"], body["finding"]) == ("completed", "record_found")
    assert body["source_id"] == "nppes_api"
    assert body["summary"] == f"NPI {NPI} is active in NPPES (record last updated 2026-01-10)"
    assert body["details"]["name_match"] == "exact"
    assert body["provenance"]["method"] == "official_api"
    assert body["freshness"] == "live"
    assert body["matched_records"] == []
    assert live.lookups == [NPI]
    assert [entry["id"] for entry in history.json()["items"]] == [body["id"]]


async def test_not_in_nppes_is_its_own_finding_never_a_404() -> None:
    container, _ = _world(api_no_result(fetched_at=NOW + timedelta(seconds=1)))

    async for client in _client(container):
        provider_id = await _register(client, NPI)
        response = await client.post(f"/providers/{provider_id}/verifications/npi", headers=HEADERS)

    assert response.status_code == 200
    assert (response.json()["finding"], response.json()["details"]["npi_status"]) == (
        "no_record",
        "not_found",
    )


async def test_a_provider_without_an_npi_is_a_manual_step() -> None:
    container, live = _world(api_no_result(fetched_at=NOW))

    async for client in _client(container):
        provider_id = await _register(client, None)
        response = await client.post(f"/providers/{provider_id}/verifications/npi", headers=HEADERS)

    assert response.json()["outcome"] == "manual_required"
    assert response.json()["finding"] is None
    assert live.lookups == []


async def test_an_unknown_provider_is_404() -> None:
    container, _ = _world(api_no_result(fetched_at=NOW))

    async for client in _client(container):
        response = await client.post(f"/providers/{uuid4()}/verifications/npi", headers=HEADERS)

    assert response.status_code == 404


# --- The paged history -----------------------------------------------------------------------


def _stored(
    provider_id: str,
    at: datetime,
    source_id: str = "fake_federal",
    credential_type: CredentialType = CredentialType.EXCLUSION,
) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        organization_id=ORGANIZATION,
        provider_id=UUID(provider_id),
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


def _history_world() -> tuple[AppContainer, InMemoryVerifications]:
    base = _container()
    verifications = InMemoryVerifications()
    container = replace(
        base,
        verification_history=VerificationHistory(base.providers, verifications, InMemoryRecords()),
    )
    return container, verifications


async def test_the_history_comes_ten_moments_at_a_time_and_a_run_is_never_split() -> None:
    container, verifications = _history_world()

    async for client in _client(container):
        provider_id = await _register(client, None)
        npi = _stored(provider_id, NOW, "fake_npi", CredentialType.NPI)
        await verifications.add(npi)
        for day in range(1, 12):
            at = NOW + timedelta(days=day)
            await verifications.add_many(
                [_stored(provider_id, at, source) for source in ("fake_federal", "fake_state")]
            )
        url = f"/providers/{provider_id}/verifications"
        first = (await client.get(url, headers=HEADERS)).json()
        second = (
            await client.get(url, params={"before": first["next_before"]}, headers=HEADERS)
        ).json()
        small = (await client.get(url, params={"limit": 1}, headers=HEADERS)).json()
        latest = await client.get(f"{url}/npi/latest", headers=HEADERS)

    assert len(first["items"]) == 20
    assert first["retention_days"] == HISTORY_RETENTION.days == 183
    assert datetime.fromisoformat(first["next_before"]) == NOW + timedelta(days=2)
    # The older page: the first day's run (both lists) and the NPI verification before it.
    assert [datetime.fromisoformat(item["created_at"]) for item in second["items"]] == [
        NOW + timedelta(days=1),
        NOW + timedelta(days=1),
        NOW,
    ]
    assert second["items"][2]["id"] == str(npi.id)
    assert second["next_before"] is None
    assert len(small["items"]) == 2
    assert {item["source_id"] for item in small["items"]} == {"fake_federal", "fake_state"}
    assert latest.status_code == 200
    assert latest.json()["id"] == str(npi.id)


async def test_without_an_npi_verification_the_latest_is_null() -> None:
    container, _ = _history_world()

    async for client in _client(container):
        provider_id = await _register(client, None)
        latest = await client.get(
            f"/providers/{provider_id}/verifications/npi/latest", headers=HEADERS
        )
        unknown = await client.get(
            f"/providers/{uuid4()}/verifications/npi/latest", headers=HEADERS
        )

    assert (latest.status_code, latest.json()) == (200, None)
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "params",
    [{"limit": "0"}, {"limit": "51"}, {"before": "2026-11-01T10:00:00"}, {"before": "soon"}],
)
async def test_a_page_outside_the_bounds_is_422(params: dict[str, str]) -> None:
    container, _ = _history_world()

    async for client in _client(container):
        provider_id = await _register(client, None)
        response = await client.get(
            f"/providers/{provider_id}/verifications", params=params, headers=HEADERS
        )

    assert response.status_code == 422
