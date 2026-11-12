from uuid import uuid4

import pytest

from credentialing.application.list_providers import MAX_PAGE_SIZE, ListProviders
from credentialing.application.provider_status import ProviderStatusKeeper
from credentialing.connectors.registry import ConnectorRegistry
from tests.fakes import InMemoryProviders, InMemoryVerifications
from tests.synthetic import individual

pytestmark = pytest.mark.anyio


def _use_case(providers: InMemoryProviders) -> ListProviders:
    keeper = ProviderStatusKeeper(providers, InMemoryVerifications(), ConnectorRegistry())
    return ListProviders(providers, keeper)


async def test_page_and_size_are_clamped_and_the_query_trimmed() -> None:
    organization_id = uuid4()
    providers = InMemoryProviders(
        individual(last="Clampperson", organization_id=organization_id),
        individual(last="Otherperson", organization_id=organization_id),
    )
    use_case = _use_case(providers)

    page = await use_case.run(organization_id, query="  clamp ", page=0, page_size=500)

    assert (page.page, page.page_size, page.total) == (1, MAX_PAGE_SIZE, 1)
    names = [item.provider.person_name for item in page.items]
    assert [name.last for name in names if name] == ["Clampperson"]


async def test_a_blank_query_lists_everyone() -> None:
    organization_id = uuid4()
    providers = InMemoryProviders(
        individual(organization_id=organization_id), individual(organization_id=organization_id)
    )

    page = await _use_case(providers).run(organization_id, query="   ", page=1, page_size=10)

    assert page.total == 2
