from datetime import timedelta

import pytest

from credentialing.connectors.registry import ConnectorRegistry, DuplicateSourceError
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    FetchResult,
    Provider,
    SourceSnapshot,
    VerificationResult,
)


def _capabilities(
    source_id: str, jurisdictions: set[str], credential_type: CredentialType
) -> ConnectorCapabilities:
    return ConnectorCapabilities(
        source_id=source_id,
        credential_type=credential_type,
        jurisdictions=frozenset(jurisdictions),
        access_policy=AccessPolicy.HTTP,
        change_signal=ChangeSignal.CONTENT_HASH,
        method=VerificationMethod.BULK_DATASET,
        poll_interval=timedelta(days=7),
        source_url="https://example.test/list",
        display_name=source_id,
    )


class FakeBulk:
    def __init__(self, capabilities: ConnectorCapabilities) -> None:
        self._capabilities = capabilities

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return self._capabilities

    async def fetch(self, previous: SourceSnapshot | None) -> FetchResult:
        raise NotImplementedError


class FakeLookup:
    def __init__(self, capabilities: ConnectorCapabilities) -> None:
        self._capabilities = capabilities

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return self._capabilities

    async def verify(self, provider: Provider) -> VerificationResult:
        raise NotImplementedError


FEDERAL_LIST = FakeBulk(_capabilities("fake_federal", {"US"}, CredentialType.EXCLUSION))
STATE_LIST = FakeBulk(_capabilities("fake_ny_list", {"NY"}, CredentialType.EXCLUSION))
STATE_LOOKUP = FakeLookup(_capabilities("fake_ny_board", {"NY"}, CredentialType.STATE_LICENSE))


@pytest.fixture
def registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    for connector in (FEDERAL_LIST, STATE_LIST, STATE_LOOKUP):
        registry.register(connector)
    return registry


def test_federal_connectors_apply_to_every_state(registry: ConnectorRegistry) -> None:
    assert registry.bulk_connectors_for(CredentialType.EXCLUSION, "NY") == [
        FEDERAL_LIST,
        STATE_LIST,
    ]
    assert registry.bulk_connectors_for(CredentialType.EXCLUSION, "CA") == [FEDERAL_LIST]
    assert registry.bulk_connectors_for(CredentialType.EXCLUSION, "US") == [FEDERAL_LIST]


def test_connectors_are_selected_by_kind_and_credential_type(registry: ConnectorRegistry) -> None:
    assert registry.lookup_connectors_for(CredentialType.STATE_LICENSE, "NY") == [STATE_LOOKUP]
    assert registry.lookup_connectors_for(CredentialType.STATE_LICENSE, "NJ") == []
    assert registry.lookup_connectors_for(CredentialType.EXCLUSION, "NY") == []
    assert registry.bulk_connectors_for(CredentialType.STATE_LICENSE, "NY") == []


def test_get_by_source_id(registry: ConnectorRegistry) -> None:
    assert registry.get("fake_ny_list") is STATE_LIST
    assert registry.get("fake_unknown") is None


def test_a_source_id_can_be_registered_once(registry: ConnectorRegistry) -> None:
    duplicate = FakeLookup(_capabilities("fake_ny_list", {"NY"}, CredentialType.EXCLUSION))

    with pytest.raises(DuplicateSourceError, match="fake_ny_list"):
        registry.register(duplicate)
