from pathlib import Path

from credentialing.bootstrap import build_registry, create_browser_fetcher
from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.browser import ChromeFetcher
from credentialing.infrastructure.config import Settings
from tests.fakes import FixedClock, ScriptedFetcher
from tests.synthetic import NOW


def test_the_browser_goes_only_to_connectors_that_need_it() -> None:
    registry = build_registry(ScriptedFetcher(), browser=ScriptedFetcher())

    with_browser = {
        connector.capabilities.source_id
        for connector in registry.bulk_connectors()
        if isinstance(connector, BulkListConnectorBase) and connector.uses_browser
    }

    assert with_browser == {"wy_wdh"}


def test_without_a_browser_no_connector_uses_one() -> None:
    connectors = build_registry(ScriptedFetcher()).bulk_connectors()

    assert not any(isinstance(c, BulkListConnectorBase) and c.uses_browser for c in connectors)


def test_the_browser_fallback_follows_the_settings() -> None:
    clock = FixedClock(NOW)
    enabled = Settings(_env_file=None, browser_profile_dir=Path("data/test-profile"))

    assert isinstance(create_browser_fetcher(enabled, clock), ChromeFetcher)
    assert (
        create_browser_fetcher(
            enabled.model_copy(update={"browser_fallback_enabled": False}), clock
        )
        is None
    )
