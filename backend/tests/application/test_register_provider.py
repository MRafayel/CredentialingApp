from uuid import uuid4

import pytest

from credentialing.application.errors import InvalidProviderInputError
from credentialing.application.register_provider import ProviderInput, RegisterProvider
from credentialing.domain.enums import EntityKind
from credentialing.domain.errors import DuplicateNpiError
from credentialing.domain.models import PersonName
from tests.fakes import FakeFirstChecker, FixedClock, InMemoryProviders
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


async def test_an_npi_already_on_the_roster_is_refused_naming_its_holder() -> None:
    providers = InMemoryProviders()
    organization_id = uuid4()
    register = RegisterProvider(providers, FixedClock(NOW))
    data = ProviderInput(
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Testperson", first="Avery"),
        npi=make_npi("100000001"),
    )
    holder = await register.run(organization_id, data)

    with pytest.raises(DuplicateNpiError) as caught:
        await register.run(organization_id, data)
    elsewhere = await register.run(uuid4(), data)

    assert caught.value.existing_provider_id == holder.id
    assert caught.value.organization_id == organization_id
    assert set(providers.stored) == {holder.id, elsewhere.id}


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


PERSON = ProviderInput(
    entity_kind=EntityKind.INDIVIDUAL, person_name=PersonName(last="Testperson", first="Avery")
)


async def test_a_new_provider_gets_its_first_check_under_the_callers_organization() -> None:
    providers, checks = InMemoryProviders(), FakeFirstChecker()
    organization_id = uuid4()

    provider = await RegisterProvider(providers, FixedClock(NOW), first_check=checks).run(
        organization_id, PERSON
    )

    assert checks.calls == [(organization_id, (provider.id,), "add", True)]


async def test_a_failed_first_check_leaves_the_provider_stored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    providers, checks = InMemoryProviders(), FakeFirstChecker(fail=RuntimeError("synthetic outage"))

    provider = await RegisterProvider(providers, FixedClock(NOW), first_check=checks).run(
        uuid4(), PERSON
    )

    assert providers.stored == {provider.id: provider}
    assert "error=RuntimeError" in caplog.text
    assert "Testperson" not in caplog.text
