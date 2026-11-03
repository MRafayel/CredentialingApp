import hashlib
from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, NotModified
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.oh_medicaid import OhMedicaidConnector
from credentialing.connectors.oh_medicaid.connector import (
    CAPABILITIES,
    INDIVIDUAL_COLUMNS,
    LANDING_URL,
    ORGANIZATION_COLUMNS,
    parse_provider_id,
    parse_zip,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    EntityKind,
    ExclusionAction,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders, fixture_bytes, oh_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

FILE_URL = (
    "https://dam.assets.ohio.gov/raw/upload/medicaid.ohio.gov/Providers/"
    "Enrollment%20and%20Support/Enrollment/ExclusionSuspensionList_09_15_2026-2.xlsx"
)

SNAPSHOT = SourceSnapshot(
    id=uuid4(), source_id="oh_medicaid", fetched_at=NOW, outcome=Outcome.COMPLETED,
    source_url=FILE_URL,
)  # fmt: skip


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return OhMedicaidConnector(ScriptedFetcher()).parse(oh_builders.oh_medicaid(), SNAPSHOT)


def _person(parsed: ParsedList, last_cell: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw.get("Last Name") == last_cell]
    return record


def _organization(parsed: ParsedList, name_cell: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw.get("Organization Name") == name_cell]
    return record


def test_the_fixture_uses_the_connectors_columns() -> None:
    assert oh_builders.INDIVIDUAL_COLUMNS == INDIVIDUAL_COLUMNS
    assert oh_builders.ORGANIZATION_COLUMNS == ORGANIZATION_COLUMNS


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 16
    assert parsed.skipped == 1
    assert parsed.merged == 1
    assert len(parsed.records) == 14
    assert parsed.unreadable == {"Individuals Action Date": 1}
    assert parsed.note() == (
        "16 rows, 14 records, 1 skipped; 1 merged; unreadable values: Individuals Action Date 1"
    )


def test_the_sheet_decides_person_or_organization(parsed: ParsedList) -> None:
    kinds = [r.entity_kind for r in parsed.records]

    assert kinds.count(EntityKind.INDIVIDUAL) == 11
    assert kinds.count(EntityKind.ORGANIZATION) == 3


def test_a_person_with_npi_date_of_birth_and_provider_number(parsed: ParsedList) -> None:
    record = _person(parsed, "TESTPERSON")

    assert record.person_name == PersonName(last="TESTPERSON", first="AVERY", middle="QUINN")
    assert record.npi == builders.NPI_A
    assert record.date_of_birth == date(1971, 2, 3)
    assert record.state_provider_id == "0123456"
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "Excluded"
    assert record.effective_date == date(2024, 5, 6)
    assert record.end_date is None
    assert record.address is None
    assert record.raw["Date Added"] == "2024-06-01"
    assert record.raw["Provider Type"] == "38 - Certified Nurse Practitioner"


def test_a_former_name_in_parentheses_is_an_alias(parsed: ParsedList) -> None:
    record = _person(parsed, "MOCKWELL (PRIORNAME)")

    assert record.person_name == PersonName(last="MOCKWELL", first="RILEY", middle="DAWN")
    assert record.aliases == ("PRIORNAME, RILEY DAWN",)
    assert record.npi is None


def test_a_name_after_aka_is_an_alias_and_a_numeric_provider_number_is_kept(
    parsed: ParsedList,
) -> None:
    record = _person(parsed, "SAMPLETON aka FORMERTON")

    assert record.person_name == PersonName(last="SAMPLETON", first="JORDAN")
    assert record.aliases == ("FORMERTON, JORDAN",)
    assert record.state_provider_id == "2345678"


@pytest.mark.parametrize(
    ("last_cell", "person"),
    [
        (
            "EXAMPLEDOC, Jr.",
            PersonName(last="EXAMPLEDOC", first="MORGAN", middle="LEE", suffix="Jr."),
        ),
        ("NURSEMOCK JR", PersonName(last="NURSEMOCK", first="CASEY", suffix="JR")),
    ],
)
def test_a_suffix_is_split_off_the_last_name(
    parsed: ParsedList, last_cell: str, person: PersonName
) -> None:
    assert _person(parsed, last_cell).person_name == person


def test_a_suspension(parsed: ParsedList) -> None:
    record = _person(parsed, "EXAMPLEDOC, Jr.")

    assert record.action is ExclusionAction.SUSPENSION
    assert record.action_raw == "Suspended"


def test_a_quoted_nickname_is_an_alias(parsed: ParsedList) -> None:
    record = _person(parsed, "NICKMOCK")

    assert record.person_name == PersonName(last="NICKMOCK", first="ALEXANDRA")
    assert record.aliases == ("NICKMOCK, ALEX",)


def test_a_trailing_initial_without_a_middle_name_is_the_middle_name(
    parsed: ParsedList,
) -> None:
    record = _person(parsed, "INITIALMOCK")

    assert record.person_name == PersonName(last="INITIALMOCK", first="JAMIE", middle="R.")


def test_several_npis_and_provider_numbers(parsed: ParsedList) -> None:
    record = _person(parsed, "LISTMOCK")

    assert record.npi == builders.NPI_B
    assert record.related_npis == (builders.NPI_C,)
    assert record.state_provider_id == "0123456"
    assert record.raw["Provider ID"] == "123456, 3456789"
    assert record.raw["Date Added"] == "Updated - 1/2/25"


def test_dates_typed_as_text_and_a_lower_case_status(parsed: ParsedList) -> None:
    record = _person(parsed, "TEXTDATEMOCK")

    assert record.date_of_birth == date(1970, 3, 4)
    assert record.effective_date == date(2020, 5, 6)
    assert record.action is ExclusionAction.SUSPENSION
    assert record.action_raw == "suspended"
    assert record.state_provider_id is None


def test_an_empty_status_is_other(parsed: ParsedList) -> None:
    record = _person(parsed, "NOSTATUSMOCK")

    assert record.action is ExclusionAction.OTHER
    assert record.action_raw is None


def test_an_unreadable_date_is_left_empty(parsed: ParsedList) -> None:
    record = _person(parsed, "BADDATEMOCK")

    assert record.effective_date is None
    assert record.date_of_birth is None
    assert record.raw["Action Date"] == "13/45/2020"


def test_an_organization_with_a_trade_name_and_an_address(parsed: ParsedList) -> None:
    record = _organization(parsed, "SAMPLE CARE SERVICES, LLC dba SAMPLE HOME HEALTH")

    assert record.organization_name == "SAMPLE CARE SERVICES, LLC"
    assert record.person_name is None
    assert record.aliases == ("SAMPLE HOME HEALTH",)
    assert record.npi == builders.NPI_D
    assert record.state_provider_id == "0234567"
    assert record.address == Address(
        line1="100 Example Rd", line2="Suite 200", city="Columbus", state="OH",
        postal_code="43004",
    )  # fmt: skip
    assert record.date_of_birth is None


def test_rows_differing_only_in_provider_type_are_one_record(parsed: ParsedList) -> None:
    record = _organization(parsed, "SAMPLE CARE SERVICES, LLC dba SAMPLE HOME HEALTH")

    assert record.raw["Provider Type"] == (
        "60 - Medicare Certified Home Health Agency; 45 - WAIVERED SERVICES ORGANIZATION"
    )


def test_a_parenthesized_name_another_state_and_a_misspelt_suspension(
    parsed: ParsedList,
) -> None:
    record = _organization(parsed, "Mockridge Wellness Center (MWC)")

    assert record.organization_name == "Mockridge Wellness Center"
    assert record.aliases == ("MWC",)
    assert record.npi == builders.NPI_E
    assert record.state_provider_id == "0345678"
    assert record.address == Address(
        line1="9 Sample St", city="Kansas City", state="MO", postal_code="64101"
    )
    assert record.action is ExclusionAction.SUSPENSION
    assert record.action_raw == "Susepnded"


def test_a_professional_corporation_stays_an_organization(parsed: ParsedList) -> None:
    record = _organization(parsed, "SAMPLEPERSON AVERY DO LLC dba MOCK FAMILY CARE")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "SAMPLEPERSON AVERY DO LLC"
    assert record.aliases == ("MOCK FAMILY CARE",)
    assert record.action is ExclusionAction.TERMINATION
    assert record.effective_date is None


def test_a_zip_formatted_as_a_date_is_read_back(parsed: ParsedList) -> None:
    record = _organization(parsed, "SAMPLEPERSON AVERY DO LLC dba MOCK FAMILY CARE")

    assert record.address is not None
    assert record.address.postal_code == "43215"


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("0123456", "0123456"),
        ("123456", "0123456"),
        ("12345", "0012345"),
        ("2345678, 3456789", "2345678"),
        ("2345678/ 3456789", "2345678"),
        ("2345678,3456789 ", "2345678"),
        ("REG ID 123456", None),
        ("12345678", None),
        ("1234", None),
        ("", None),
    ],
)
def test_provider_numbers(cell: str, expected: str | None) -> None:
    assert parse_provider_id(cell) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("2018-05-25", "43245"), ("43215", "43215"), (" 64101 ", "64101"), ("", "")],
)
def test_zip_cells(cell: str, expected: str) -> None:
    assert parse_zip(cell) == expected


def test_a_workbook_without_the_organizations_sheet_fails() -> None:
    person = ("SAMPLE", "AVERY", "", "", "", "", "Excluded", "1/2/2020", "", "")
    body = builders.workbook([INDIVIDUAL_COLUMNS, person], title="Individuals")

    with pytest.raises(ParseError, match="no sheet named 'Organizations'"):
        OhMedicaidConnector(ScriptedFetcher()).parse(body, SNAPSHOT)


def test_capabilities_are_polite_and_discover_the_link() -> None:
    connector = OhMedicaidConnector(ScriptedFetcher(), browser=ScriptedFetcher())

    assert CAPABILITIES.source_id == "oh_medicaid"
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.source_url == LANDING_URL
    assert CAPABILITIES.source_url.startswith("https://")
    assert CAPABILITIES.jurisdictions == frozenset({"OH"})
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.access_policy is AccessPolicy.HTTP
    assert CAPABILITIES.method is VerificationMethod.BULK_DATASET
    assert CAPABILITIES.browser_fallback is True
    assert CAPABILITIES.provider_type_fields == ("Provider Type",)
    assert CAPABILITIES.display_name == "Ohio Medicaid Provider Exclusion and Suspension List"
    assert connector.landing_url == LANDING_URL
    assert connector.uses_browser


def test_the_list_link_is_found_among_other_files() -> None:
    page = fixture_bytes("oh_medicaid_landing.html").decode()

    assert OhMedicaidConnector(ScriptedFetcher()).find_link(page) == FILE_URL


def test_a_page_without_the_list_link_raises() -> None:
    page = '<a href="https://dam.assets.ohio.gov/raw/upload/x/ProviderTypeCrosswalk.xlsx">x</a>'

    with pytest.raises(ParseError, match="no link matching"):
        OhMedicaidConnector(ScriptedFetcher()).find_link(page)


_FAKE_404 = FetchFailure(
    outcome=Outcome.FAILED, status=404, note="unexpected HTTP 404", fetched_at=NOW
)
"""CloudFront's static 404 page, served to scripts in place of the landing page."""


def _document(body: bytes, url: str, *, in_browser: bool = False) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag='"v1"' if not in_browser else None,
        last_modified=None,
        content_type="text/html; charset=UTF-8" if in_browser else None,
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=in_browser,
    )


def _landing_in_browser() -> FetchedDocument:
    return _document(fixture_bytes("oh_medicaid_landing.html"), LANDING_URL, in_browser=True)


@pytest.mark.anyio
async def test_a_landing_page_hidden_behind_a_404_is_read_in_the_browser() -> None:
    http = ScriptedFetcher(_FAKE_404, _document(oh_builders.oh_medicaid(), FILE_URL))
    browser = ScriptedFetcher(_landing_in_browser())

    result = await OhMedicaidConnector(http, browser=browser).fetch(None)

    assert http.requests == [
        (LANDING_URL, ChangeSignal.CONTENT_HASH),
        (FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert browser.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == FILE_URL
    assert result.snapshot.note == (
        "browser fallback; 16 rows, 14 records, 1 skipped; 1 merged; "
        "unreadable values: Individuals Action Date 1"
    )
    assert result.records is not None
    assert len(result.records) == 14


@pytest.mark.anyio
async def test_an_unchanged_link_is_the_whole_poll() -> None:
    http = ScriptedFetcher(_document(fixture_bytes("oh_medicaid_landing.html"), LANDING_URL))

    result = await OhMedicaidConnector(http, browser=ScriptedFetcher()).fetch(SNAPSHOT)

    assert http.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (link unchanged)"
    assert result.records is None


@pytest.mark.anyio
async def test_a_known_link_is_downloaded_conditionally() -> None:
    previous = SNAPSHOT.model_copy(update={"outcome": Outcome.FAILED})
    unchanged = NotModified(
        reason="304", etag='"v1"', last_modified=None, fetched_at=NOW, status=304
    )
    page = _document(fixture_bytes("oh_medicaid_landing.html"), LANDING_URL)
    http = ScriptedFetcher(page, unchanged)

    await OhMedicaidConnector(http).fetch(previous)

    assert http.requests[1] == (FILE_URL, ChangeSignal.CONDITIONAL_GET)


@pytest.mark.anyio
async def test_a_404_without_a_browser_is_failed_not_empty() -> None:
    result = await OhMedicaidConnector(ScriptedFetcher(_FAKE_404)).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.records is None


@pytest.mark.anyio
async def test_a_browser_that_is_refused_too_stays_blocked_with_both_notes() -> None:
    refused = FetchFailure(
        outcome=Outcome.BLOCKED,
        status=403,
        note="HTTP 403 in the browser",
        fetched_at=NOW,
    )

    result = await OhMedicaidConnector(
        ScriptedFetcher(_FAKE_404), browser=ScriptedFetcher(refused)
    ).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == (
        "unexpected HTTP 404; browser fallback: HTTP 403 in the browser"
    )
    assert result.records is None
