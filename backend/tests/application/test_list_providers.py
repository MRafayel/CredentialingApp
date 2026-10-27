from uuid import uuid4

import pytest

from credentialing.application.list_providers import MAX_PAGE_SIZE, ListProviders
from tests.fakes import InMemoryProviders
from tests.synthetic import individual

pytestmark = pytest.mark.anyio


async def test_page_and_size_are_clamped_and_the_query_trimmed() -> None:
    organization_id = uuid4()
    providers = InMemoryProviders(
        individual(last="Clampperson", organization_id=organization_id),
        individual(last="Otherperson", organization_id=organization_id),
    )
    use_case = ListProviders(providers)

    page = await use_case.run(organization_id, query="  clamp ", page=0, page_size=500)

    assert (page.page, page.page_size, page.total) == (1, MAX_PAGE_SIZE, 1)
    assert [p.person_name.last for p in page.items if p.person_name] == ["Clampperson"]


async def test_a_blank_query_lists_everyone() -> None:
    organization_id = uuid4()
    providers = InMemoryProviders(
        individual(organization_id=organization_id), individual(organization_id=organization_id)
    )

    page = await ListProviders(providers).run(organization_id, query="   ", page=1, page_size=10)

    assert page.total == 2
