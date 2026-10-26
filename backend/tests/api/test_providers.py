"""API behaviour over in-memory use cases: fast, no database."""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx2
import pytest

from credentialing.api.app import create_app
from credentialing.api.container import AppContainer
from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.verification_history import VerificationHistory
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    FixedClock,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
)
from tests.synthetic import NOW, make_npi

pytestmark = pytest.mark.anyio

ORGANIZATION = UUID("00000000-0000-0000-0000-000000000001")
HEADERS = {"X-Organization-Id": str(ORGANIZATION)}


def _container(*, header_enabled: bool = True) -> AppContainer:
    clock = FixedClock(NOW)
    providers, records, verifications = (
        InMemoryProviders(),
        InMemoryRecords(),
        InMemoryVerifications(),
    )
    return AppContainer(
        register_provider=RegisterProvider(providers, clock),
        check_exclusions=CheckExclusions(
            ConnectorRegistry(),
            providers,
            records,
            InMemorySnapshots(),
            verifications,
            DEFAULT_FRESHNESS,
            clock,
        ),
        verification_history=VerificationHistory(providers, verifications, records),
        providers=providers,
        organization_header_enabled=header_enabled,
    )


async def _client(container: AppContainer) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(expose_docs=False)
    app.state.container = container
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    async for client in _client(_container()):
        yield client


async def test_register_then_get(client: httpx2.AsyncClient) -> None:
    body = {
        "entity_kind": "individual",
        "person_name": {"last": "Testperson", "first": "Avery"},
        "npi": make_npi("100000001"),
        "date_of_birth": "1970-01-01",
        "licenses": [{"number": "123456", "state": "NY"}],
    }

    created = await client.post("/providers", json=body, headers=HEADERS)

    assert created.status_code == 201
    payload = created.json()
    assert payload["person_name"]["last"] == "Testperson"
    assert payload["has_date_of_birth"] is True
    assert "date_of_birth" not in payload
    fetched = await client.get(f"/providers/{payload['id']}", headers=HEADERS)
    assert fetched.status_code == 200
    assert fetched.json() == payload


async def test_another_organization_cannot_read_the_provider(client: httpx2.AsyncClient) -> None:
    body = {"entity_kind": "organization", "organization_name": "Sampleville Care Group"}
    created = (await client.post("/providers", json=body, headers=HEADERS)).json()

    other = {"X-Organization-Id": str(uuid4())}
    response = await client.get(f"/providers/{created['id']}", headers=other)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"entity_kind": "individual"},
        {"entity_kind": "organization", "organization_name": "X", "npi": "1234567890"},
        {"entity_kind": "individual", "person_name": {"last": "", "first": "A"}},
        {"entity_kind": "robot", "organization_name": "X"},
        {"entity_kind": "organization", "organization_name": "X", "ssn": "000-00-0000"},
    ],
)
async def test_invalid_bodies_are_422(client: httpx2.AsyncClient, body: dict[str, object]) -> None:
    response = await client.post("/providers", json=body, headers=HEADERS)

    assert response.status_code == 422


async def test_unknown_provider_is_404(client: httpx2.AsyncClient) -> None:
    for response in (
        await client.get(f"/providers/{uuid4()}", headers=HEADERS),
        await client.post(f"/providers/{uuid4()}/verifications/exclusions", headers=HEADERS),
        await client.get(f"/providers/{uuid4()}/verifications", headers=HEADERS),
    ):
        assert response.status_code == 404


async def test_the_organization_header_is_required(client: httpx2.AsyncClient) -> None:
    response = await client.get(f"/providers/{uuid4()}")

    assert response.status_code == 401


async def test_outside_local_development_the_header_is_refused() -> None:
    async for client in _client(_container(header_enabled=False)):
        response = await client.get(f"/providers/{uuid4()}", headers=HEADERS)

        assert response.status_code == 401
        assert "not configured" in response.json()["detail"]
