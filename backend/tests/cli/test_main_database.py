"""The ``sources`` command end to end over the local database; it only reads."""

import json

import pytest

from credentialing.bootstrap import build_registry
from credentialing.cli import main
from tests.fakes import ScriptedFetcher

pytestmark = pytest.mark.integration


def test_sources_json_lists_every_registered_source(capsys: pytest.CaptureFixture[str]) -> None:
    registered = [
        c.capabilities.source_id for c in build_registry(ScriptedFetcher()).bulk_connectors()
    ]

    code = main(["sources", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert sorted(item["source_id"] for item in payload) == sorted(registered)


def test_sources_table_ends_with_the_policy_line(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["sources"])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[-1].startswith("Data freshness policy: exclusions ")
