from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.in_fssa import InFssaConnector
from credentialing.connectors.in_fssa.connector import first_location, parse_provider_name
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="in_fssa",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=InFssaConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return InFssaConnector(ScriptedFetcher()).parse(builders.in_fssa(), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["Provider Name"] == text)


def test_every_row_is_a_termination(parsed: ParsedList) -> None:
    assert parsed.rows == 6
    assert len(parsed.records) == 6
    assert {r.action for r in parsed.records} == {ExclusionAction.TERMINATION}


def test_a_person_first_name_first_without_npi(parsed: ParsedList) -> None:
    record = _named(parsed, "Avery Testperson")

    assert record.person_name == PersonName(last="Testperson", first="Avery")
    assert record.npi is None
    assert record.address == Address(city="Indianapolis", state="IN")
    assert record.effective_date == date(2015, 9, 17)


def test_a_credential_after_the_comma_two_npis_and_several_locations(
    parsed: ParsedList,
) -> None:
    record = _named(parsed, "Jordan L. Exampledoc, MD")

    assert record.person_name == PersonName(
        last="Exampledoc", first="Jordan", middle="L.", suffix="MD"
    )
    assert record.npi == builders.NPI_A
    assert record.related_npis == (builders.NPI_B,)
    assert record.address == Address(city="Sampleville", state="IN")


def test_businesses_including_one_word_names(parsed: ParsedList) -> None:
    for name in ("Sample Medical Laboratories", "Samplecorp"):
        record = _named(parsed, name)
        assert record.entity_kind is EntityKind.ORGANIZATION
        assert record.organization_name == name


def test_text_dates_and_locations_after_runs_of_spaces(parsed: ParsedList) -> None:
    podiatrist = _named(parsed, "Riley Mockperson, Podiatrist")
    dentist = _named(parsed, "Casey Mc Example, DDS")

    assert podiatrist.person_name == PersonName(
        last="Mockperson", first="Riley", suffix="Podiatrist"
    )
    assert podiatrist.address == Address(city="Gary", state="IN")
    assert dentist.person_name == PersonName(last="Mc Example", first="Casey", suffix="DDS")
    assert dentist.effective_date == date(2019, 7, 4)
    assert dentist.address == Address(city="Phoenix", state="AZ")


def test_parse_provider_name_keeps_both_credentials() -> None:
    parsed = parse_provider_name("John K Sample Jr., MD")

    assert parsed.person == PersonName(last="Sample", first="John", middle="K", suffix="Jr. MD")
    assert parse_provider_name("Fast Sample Technologies, LLC").organization == (
        "Fast Sample Technologies, LLC"
    )


def test_first_location() -> None:
    assert first_location("Gary, IN\nHammond, IN") == "Gary, IN"
    assert first_location("\nGary, IN") == "Gary, IN"
    assert first_location(None) is None
