from uuid import uuid4

import pytest

from credentialing.application.errors import InvalidProviderInputError
from credentialing.application.register_provider import ProviderInput, RegisterProvider
from credentialing.domain.enums import EntityKind
from credentialing.domain.models import PersonName
from tests.fakes import FixedClock, InMemoryProviders
from tests.synthetic import NOW, make_npi

pytestmark = pytest.mark.anyio


async def test_a_provider_is_created_and_stored() -> None:
    providers = InMemoryProviders()
    organization_id = uuid4()
    data = ProviderInput(
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Testperson", first="Avery"),
        npi=make_npi("100000001"),
    )

    provider = await RegisterProvider(providers, FixedClock(NOW)).run(organization_id, data)

    assert provider.organization_id == organization_id
    assert provider.created_at == NOW
    assert providers.stored[provider.id] == provider


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (ProviderInput(entity_kind=EntityKind.INDIVIDUAL), "needs a person name"),
        (ProviderInput(entity_kind=EntityKind.ORGANIZATION), "needs an organization name"),
        (
            ProviderInput(
                entity_kind=EntityKind.ORGANIZATION, organization_name="X", npi="1234567890"
            ),
            "not a valid ten-digit NPI",
        ),
        (ProviderInput(entity_kind=EntityKind.UNKNOWN), "individual or organization"),
    ],
)
async def test_invalid_input_is_rejected(data: ProviderInput, message: str) -> None:
    providers = InMemoryProviders()

    with pytest.raises(InvalidProviderInputError, match=message):
        await RegisterProvider(providers, FixedClock(NOW)).run(uuid4(), data)
    assert providers.stored == {}
