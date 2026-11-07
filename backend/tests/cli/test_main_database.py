"""The ``sources`` command end to end over the local database; it only reads."""

import json

import pytest

from credentialing.bootstrap import build_registry
from credentialing.cli import main
from tests.fakes import ScriptedFetcher

pytestmark = pytest.mark.integration


def test_sources_json_lists_every_registered_source(capsys: pytest.CaptureFixture[str]) -> None:
    registered = [
        c.capabilities.source_id for c in build_registry(ScriptedFetcher()).polled_connectors()
    ]

    code = main(["sources", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert sorted(item["source_id"] for item in payload["sources"]) == sorted(registered)
    assert payload["npi_links"]["records"] >= 0


def test_sources_table_ends_with_the_policy_and_npi_links_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["sources"])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[-2].startswith("Data freshness policy: exclusions ")
    assert lines[-1].startswith("NPI links: ")


def test_link_npis_refuses_an_unknown_list_without_linking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["link-npis", "--source", "no_such_list"])

    assert code == 2
    assert "no bulk-list source is registered as 'no_such_list'" in capsys.readouterr().err
