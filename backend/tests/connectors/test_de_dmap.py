from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.de_dmap import DeDmapConnector
from credentialing.connectors.de_dmap.connector import (
    CAPABILITIES,
    DEA,
    DOWNLOAD_URL,
    Party,
    parse_party,
)
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList
from credentialing.domain.enums import ChangeSignal, EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, License, PersonName, SourceSnapshot
from tests.connectors.fixtures.de_builders import DEA_NUMBERS, NPI_A, NPI_B, NPI_C, de_dmap
from tests.connectors.fixtures.pdf_builders import build_table_pdf
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="de_dmap",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=DOWNLOAD_URL,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return DeDmapConnector(ScriptedFetcher()).parse(de_dmap(), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["Sanctioned Provider Name"] == text)


def test_both_pages_are_read_below_their_header_and_one_repeat_is_merged(
    parsed: ParsedList,
) -> None:
    assert parsed.rows == 11
    assert parsed.skipped == 0
    assert parsed.merged == 1
    assert len(parsed.records) == 10
    assert parsed.unreadable == {"Effective Date": 1, "Reinstated Date": 1}


def test_the_file_is_compared_by_hash_because_its_etag_never_changes() -> None:
    assert CAPABILITIES.change_signal is ChangeSignal.CONTENT_HASH
    assert CAPABILITIES.jurisdictions == frozenset({"DE"})
    assert DeDmapConnector.download_url == CAPABILITIES.download_url == DOWNLOAD_URL


def test_a_person_with_credential_npi_licence_and_scheduled_end(parsed: ParsedList) -> None:
    record = _named(parsed, "Avery J. Testperson, MD")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(
        last="Testperson", first="Avery", middle="J.", suffix="MD"
    )
    assert record.npi == NPI_A
    assert record.license == License(number="C1-0000001", state="DE")
    assert record.action is ExclusionAction.SANCTION
    assert record.action_raw == "Delaware Board of Medical Licensure and Discipline"
    assert record.effective_date == date(2025, 3, 15)
    assert record.end_date == date(2030, 3, 15)
    assert record.reason == "Suspended for three years with conditions"
    assert record.raw["Year"] == "2025"
    assert record.raw["Taxonomy #"] == "207Q00000X"


def test_an_organization_listed_by_the_oig_indefinitely(parsed: ParsedList) -> None:
    record = _named(parsed, "Sampleville Health Care Inc")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sampleville Health Care Inc"
    assert record.npi == NPI_B
    assert record.license is None
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "OIG"
    assert record.end_date is None


def test_a_role_is_not_an_alias_and_a_repeat_with_an_end_date_stays_open(
    parsed: ParsedList,
) -> None:
    record = _named(parsed, "Jordan Sampleton (Employee)")

    assert record.person_name == PersonName(last="Sampleton", first="Jordan")
    assert record.aliases == ()
    assert record.npi is None
    assert record.license == License(number="RN000001L", state="PA")
    assert record.action_raw == "Pennsylvania Department of Human Services"
    # One row has no reinstated date, the other one: the sanction still stands on the first.
    assert record.end_date is None
    assert record.raw["Reinstated Date"] == "6/1/2027"
    assert record.raw["Comments"] == "Second listing"


def test_a_reinstated_text_with_a_date_ends_the_listing(parsed: ParsedList) -> None:
    record = _named(parsed, "Riley Mockwell, LPN")

    assert record.person_name == PersonName(last="Mockwell", first="Riley", suffix="LPN")
    assert record.end_date == date(2015, 7, 28)
    assert record.raw["Reinstated Date"] == "Reinstated effective 07/28/2015"


def test_a_reinstated_date_before_the_effective_date_is_unreadable(parsed: ParsedList) -> None:
    record = _named(parsed, "Casey Example")

    assert record.effective_date == date(2010, 12, 2)
    assert record.end_date is None
    assert record.raw["Reinstated Date"] == "6/1/2010"


def test_a_bad_effective_date_is_unreadable_and_a_word_is_no_end_date(
    parsed: ParsedList,
) -> None:
    record = _named(parsed, "Jamie Q. Mockfield, PCA")

    assert record.person_name == PersonName(
        last="Mockfield", first="Jamie", middle="Q.", suffix="PCA"
    )
    assert record.effective_date is None
    assert record.end_date is None
    assert record.raw["Effective Date"] == "7/20/014"


def test_an_oig_row_via_pennsylvania_takes_the_authority_state_for_its_licence(
    parsed: ParsedList,
) -> None:
    record = _named(parsed, "Morgan Testwood RN, CRNP")

    assert record.person_name == PersonName(last="Testwood", first="Morgan", suffix="RN CRNP")
    assert record.npi == NPI_C
    assert record.license == License(number="RN000003", state="PA")
    assert record.action is ExclusionAction.EXCLUSION
    assert record.end_date == date(2028, 2, 3)


def test_a_pennsylvania_provider_number_is_not_a_licence(parsed: ParsedList) -> None:
    record = _named(parsed, "Taylor Sample-Person")

    assert record.license is None
    assert record.raw["License #"] == "PROMISe#: 1234567"
    assert record.end_date is None


def test_last_first_and_an_alias(parsed: ParsedList) -> None:
    assert _named(parsed, "Mockname, Drew").person_name == PersonName(last="Mockname", first="Drew")
    aka = _named(parsed, "Quinn Example-Doe, RN a.k.a. Quinn Doe")
    assert aka.person_name == PersonName(last="Example-Doe", first="Quinn", suffix="RN")
    assert aka.aliases == ("Quinn Doe",)
    assert aka.license == License(number="R2-0000004", state="DE")


def test_the_dea_number_is_never_stored(parsed: ParsedList) -> None:
    for record in parsed.records:
        assert DEA not in record.raw
        stored = record.model_dump_json()
        for number in DEA_NUMBERS:
            assert number not in stored


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        pytest.param(
            "Avery Testperson MD, DMD",
            Party(PersonName(last="Testperson", first="Avery", suffix="MD DMD"), None, ()),
            id="credentials-before-and-after-a-comma",
        ),
        pytest.param(
            "Avery Testperson, RN/LPN",
            Party(PersonName(last="Testperson", first="Avery", suffix="RN LPN"), None, ()),
            id="slash-between-credentials",
        ),
        pytest.param(
            "Avery Testperson, Massage Therapist",
            Party(PersonName(last="Testperson", first="Avery"), None, ()),
            id="profession-after-a-comma",
        ),
        pytest.param(
            "Avery Testperson (Owner/Exec)",
            Party(PersonName(last="Testperson", first="Avery"), None, ()),
            id="role-in-parentheses",
        ),
        pytest.param(
            "Jamie Doe (Maidenmock), LPN",
            Party(PersonName(last="Doe", first="Jamie", suffix="LPN"), None, ("Maidenmock",)),
            id="maiden-name-in-parentheses",
        ),
        pytest.param(
            "Avery Testperson, PHD (AKA Avery Otherperson)",
            Party(
                PersonName(last="Testperson", first="Avery", suffix="PHD"),
                None,
                ("Avery Otherperson",),
            ),
            id="alias-in-parentheses",
        ),
        pytest.param(
            "Riley Mockwell, MD #12345-67",
            Party(PersonName(last="Mockwell", first="Riley", suffix="MD"), None, ()),
            id="number-after-the-name",
        ),
        pytest.param(
            "Riley J.Mockwell",
            Party(PersonName(last="Mockwell", first="Riley", middle="J."), None, ()),
            id="initial-without-a-space",
        ),
        pytest.param(
            "Sampleville Wellness Center, LLC (Owner)",
            Party(None, "Sampleville Wellness Center, LLC", ()),
            id="organization-with-a-role",
        ),
        pytest.param(
            "AVERY TESTPERSON",
            Party(PersonName(last="TESTPERSON", first="AVERY"), None, ()),
            id="capitals-are-names",
        ),
    ],
)
def test_name_cells(cell: str, expected: Party) -> None:
    assert parse_party(cell) == expected


def test_a_pdf_without_the_header_is_a_parse_error() -> None:
    body = build_table_pdf([[["Name", "Date"], ["Avery Testperson", "1/2/2024"]]])

    with pytest.raises(ParseError):
        DeDmapConnector(ScriptedFetcher()).parse(body, SNAPSHOT)
