from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList, UnreadableRow
from credentialing.connectors.mt_dphhs import MtDphhsConnector
from credentialing.connectors.mt_dphhs.connector import COLUMNS, fold_alias_rows
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW, make_npi

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="mt_dphhs",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=MtDphhsConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return MtDphhsConnector(ScriptedFetcher()).parse(fixture_bytes("mt_dphhs.html"), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["Terminated/Excluded Provider(s)"] == text)


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 6
    assert len(parsed.records) == 5
    assert parsed.skipped == 1
    assert parsed.unreadable == {"Effective Date": 1}


def test_a_state_termination(parsed: ParsedList) -> None:
    record = _named(parsed, "Testperson, Avery")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery")
    assert record.npi == make_npi("100000001")
    assert record.action is ExclusionAction.TERMINATION
    assert record.action_raw == "State Termination"
    assert record.effective_date == date(2024, 7, 23)
    assert record.raw["Healthcare Profession"] == "Physician"
    assert record.raw["Source"] == "State Termination"


def test_aka_rows_become_aliases_of_the_row_above(parsed: ParsedList) -> None:
    record = _named(parsed, "Sampleton, Jordan K.")

    assert record.person_name == PersonName(last="Sampleton", first="Jordan", middle="K.")
    assert record.aliases == ("Jordan Mockfield", "Mockfield, Jordan K.")
    assert record.action is ExclusionAction.EXCLUSION
    assert record.raw["Aliases"] == "a.k.a. Jordan Mockfield\na.k.a. Mockfield, Jordan K."
    assert all(
        not r.raw["Terminated/Excluded Provider(s)"].startswith("a.k.a.") for r in parsed.records
    )


def test_a_maiden_name_and_a_combined_source(parsed: ParsedList) -> None:
    record = _named(parsed, "Exampleberg (Formername), Riley")

    assert record.person_name == PersonName(last="Exampleberg", first="Riley")
    assert record.aliases == ("Formername, Riley",)
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "OIG Exclusion/State Termination"


def test_an_individual_written_without_a_comma_stays_a_person(parsed: ParsedList) -> None:
    record = _named(parsed, "O\u2019Mock Casey D.")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="O\u2019Mock", first="Casey", middle="D.")
    assert record.action is ExclusionAction.TERMINATION


def test_a_business_with_entities_and_a_line_break(parsed: ParsedList) -> None:
    record = _named(parsed, "Mock & Sample Diabetic Direct")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Mock & Sample Diabetic Direct"
    assert record.effective_date is None


def test_an_alias_row_without_a_provider_above_it_is_skipped() -> None:
    page = (
        b"<table><tr><th>Terminated/Excluded Provider(s)</th><th>Healthcare Profession</th>"
        b"<th>NPI</th><th colspan='2'>Effective Date</th></tr>"
        b"<tr><td>a.k.a. Sample Name</td><td></td><td></td><td></td><td></td></tr>"
        b"<tr><td>Testperson, Avery</td><td>RN</td><td></td><td>1/2/2020</td>"
        b"<td>OIG Exclusion</td></tr></table>"
    )

    result = MtDphhsConnector(ScriptedFetcher()).parse(page, SNAPSHOT)

    assert (result.rows, len(result.records), result.skipped) == (2, 1, 1)
    assert result.records[0].aliases == ()


def test_folding_passes_unreadable_rows_on() -> None:
    broken = UnreadableRow("table row 1 has 6 cells, expected 5")
    row = dict.fromkeys(COLUMNS, "")

    assert list(fold_alias_rows([broken, row])) == [broken, row]
