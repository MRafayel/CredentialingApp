from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.ga_dch import GaDchConnector
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders, fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="ga_dch_oig",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url="https://dch.georgia.gov/document/document/sample-excluded-list/download",
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return GaDchConnector(ScriptedFetcher()).parse(builders.ga_dch_oig(), SNAPSHOT)


def _record(parsed: ParsedList, last: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["LAST NAME"] == last]
    return record


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 6
    assert len(parsed.records) == 6
    assert parsed.skipped == 0
    assert parsed.unreadable == {"SANCDATE": 1}
    assert all(r.action is ExclusionAction.EXCLUSION for r in parsed.records)


def test_dates_as_text_integers_and_excel_dates(parsed: ParsedList) -> None:
    assert _record(parsed, "TESTPERSON").effective_date == date(1982, 4, 15)
    assert _record(parsed, "EXAMPLEDOC").effective_date == date(2025, 6, 30)
    assert _record(parsed, "SAMPLEOWNER").effective_date == date(2026, 8, 27)
    assert _record(parsed, "FAKENAME").effective_date is None


def test_a_person_with_npi_and_date_of_birth(parsed: ParsedList) -> None:
    record = _record(parsed, "EXAMPLEDOC")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="EXAMPLEDOC", first="JORDAN", middle="K")
    assert record.npi == builders.NPI_A
    assert record.date_of_birth == date(1970, 1, 1)
    assert record.raw["GENERAL"] == "NURSING PROFESSION"
    assert record.raw["STATE"] == "AL, GA"


def test_a_business_row(parsed: ParsedList) -> None:
    [record] = [r for r in parsed.records if r.entity_kind is EntityKind.ORGANIZATION]

    assert record.organization_name == "SAMPLE MEDICAL EQUIPMENT, INC"
    assert record.person_name is None
    assert record.effective_date == date(2011, 10, 12)


def test_a_former_name_in_parentheses_is_an_alias(parsed: ParsedList) -> None:
    record = _record(parsed, "MOCKPERSON (MAIDENMOCK)")

    assert record.person_name == PersonName(last="MOCKPERSON", first="RILEY")
    assert record.aliases == ("MAIDENMOCK, RILEY",)


def test_a_business_next_to_a_person_is_a_related_party(parsed: ParsedList) -> None:
    record = _record(parsed, "SAMPLEOWNER")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.related_parties == ("SAMPLE TRANSPORT CO",)
    assert record.aliases == ()


def test_the_workbook_link_is_found_on_the_landing_page() -> None:
    page = fixture_bytes("ga_dch_oig_landing.html").decode()

    link = GaDchConnector(ScriptedFetcher()).find_link(page)

    assert link == (
        "/document/document/"
        "office-inspector-general-list-excluded-individuals-and-entities-october-5-2026/download"
    )


def test_a_page_without_the_list_link_raises() -> None:
    with pytest.raises(ParseError, match="no link matching"):
        GaDchConnector(ScriptedFetcher()).find_link('<a href="/document/publication/x/download">')
