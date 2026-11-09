"""The NPI verification route over in-memory use cases: fast, no database."""

from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import httpx2
import pytest

from credentialing.api.container import AppContainer
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.verification_history import VerificationHistory
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.models import NpiAnswer
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.api.test_providers import HEADERS, _client, _container
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
    assert [entry["id"] for entry in history.json()] == [body["id"]]


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
