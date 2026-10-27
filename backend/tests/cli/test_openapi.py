import json

import pytest

from credentialing.cli import main


def test_openapi_prints_the_schema_without_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["openapi"])

    schema = json.loads(capsys.readouterr().out)
    assert code == 0
    assert schema["info"]["title"] == "Credentialing API"
    assert {"/providers", "/providers/{provider_id}", "/sources", "/exclusions/search"} <= set(
        schema["paths"]
    )
    assert "get" in schema["paths"]["/providers"]
