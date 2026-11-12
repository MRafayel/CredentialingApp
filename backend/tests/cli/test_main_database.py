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
    assert payload["nppes_api"]["source_id"] == "nppes_api"
    assert "last_run" in payload["monitoring"]
    assert "nppes_api" not in registered


def test_sources_table_ends_with_the_policy_npi_links_api_and_monitoring_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["sources"])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[-4].startswith("Data freshness policy: exclusions ")
    assert lines[-3].startswith("NPI links: ")
    assert lines[-2].startswith("NPPES API: ")
    assert lines[-1].startswith("Monitoring: ")


def test_link_npis_refuses_an_unknown_list_without_linking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["link-npis", "--source", "no_such_list"])

    assert code == 2
    assert "no bulk-list source is registered as 'no_such_list'" in capsys.readouterr().err
