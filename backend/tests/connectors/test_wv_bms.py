from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.wv_bms import WvBmsConnector
from credentialing.connectors.wv_bms.connector import CAPABILITIES, LANDING_URL
from credentialing.domain.enums import ChangeSignal, EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders, fixture_bytes, wv_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="wv_bms",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url="https://www.wvmmis.com/SharepointDownload?parent=x&docname=y.pdf",
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return WvBmsConnector(ScriptedFetcher()).parse(wv_builders.wv_bms(), SNAPSHOT)


def _record(parsed: ParsedList, last_name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Last Name"] == last_name]
    return record


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 8
    assert len(parsed.records) == 8
    assert parsed.skipped == 0
    assert parsed.merged == 0
    assert parsed.unreadable == {}


def test_a_person_with_an_address_and_an_open_ended_exclusion(parsed: ParsedList) -> None:
    record = _record(parsed, "Testperson")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery")
    assert record.npi == builders.NPI_A
    assert record.address == Address(
        line1="100 Sample Street", city="Charleston", state="WV", postal_code="25301"
    )
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "Excluded Indefinitely"
    assert record.effective_date == date(2020, 1, 20)
    assert record.end_date is None
    assert record.reason == "OIG"


def test_a_row_without_a_first_name_is_a_wrapped_organization(parsed: ParsedList) -> None:
    record = _record(parsed, "Mockwell\nTesting\nLaboratories, LLC.")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Mockwell Testing Laboratories, LLC."
    assert record.person_name is None
    assert record.address == Address(
        line1="200 Example Avenue",
        line2="Suite 100",
        city="Morgantown",
        state="WV",
        postal_code="26501-1234",
    )
    assert record.action is ExclusionAction.TERMINATION
    assert record.effective_date == date(2026, 3, 2)
    assert record.reason == "License revoked by WV BOM"


def test_an_inmate_number_in_another_state_and_a_two_digit_year(parsed: ParsedList) -> None:
    record = _record(parsed, "Sampleton, Jr.")

    assert record.person_name == PersonName(
        last="Sampleton", first="Jordan", middle="Lee", suffix="Jr."
    )
    assert record.npi is None
    assert record.address == Address(
        line1="PO BOX 1000",
        line2="#12345-678",
        city="Sampletown",
        state="PA",
        postal_code="17837",
    )
    assert record.effective_date == date(2025, 1, 9)
    assert record.action is ExclusionAction.EXCLUSION


def test_both_words_are_an_exclusion_and_a_zip_wrapped_before_its_hyphen(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "Mockman II")

    assert record.person_name == PersonName(last="Mockman", first="Riley", suffix="II")
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "Excluded/ Terminated"
    assert record.address is not None
    assert record.address.postal_code == "26003-5678"


def test_a_word_broken_over_two_lines_and_an_npi_with_a_note(parsed: ParsedList) -> None:
    record = _record(parsed, "Examplewood")

    assert record.person_name == PersonName(last="Examplewood", first="Maximilianus")
    assert record.npi == builders.NPI_D
    assert record.action is ExclusionAction.TERMINATION
    assert record.action_raw == "Terminated/ Indefinitely"
    assert record.effective_date == date(2024, 11, 12)


def test_a_professional_corporation_in_the_last_name_is_its_person(parsed: ParsedList) -> None:
    record = _record(parsed, "Sampleperson MD\nInc")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Sampleperson", first="Casey", suffix="MD Inc")
    assert record.aliases == ("Casey Sampleperson MD Inc",)
    assert record.action is ExclusionAction.TERMINATION


def test_a_business_in_the_last_name_with_a_first_name_is_an_organization(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "Mockwell &\nAssociates")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Dana Mockwell & Associates"


def test_every_npi_after_the_first_is_related(parsed: ParsedList) -> None:
    record = _record(parsed, "Example\nHealth Center")

    assert record.npi == builders.NPI_F
    assert record.related_npis == (builders.NPI_A, builders.NPI_B)


def test_capabilities_discover_the_link_weekly() -> None:
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.poll_interval.days == 7
    assert WvBmsConnector.landing_url == LANDING_URL


def test_the_download_url_is_built_from_the_grid_docname() -> None:
    page = fixture_bytes("wv_bms_landing.html").decode()

    link = WvBmsConnector(ScriptedFetcher()).find_link(page)

    assert link == (
        "https://www.wvmmis.com/SharepointDownload?parent=Medicaid+Provider+SanctionedExclusion"
        "&docname=WV%20Provider%20Exclusions%20and%20Terminations%20September%202026.pdf"
    )


def test_a_page_without_the_document_has_no_link() -> None:
    with pytest.raises(ParseError):
        WvBmsConnector(ScriptedFetcher()).find_link("<html><td>Other file.pdf</td></html>")
