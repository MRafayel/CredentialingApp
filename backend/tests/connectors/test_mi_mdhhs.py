import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.mi_mdhhs import MiMdhhsConnector
from credentialing.connectors.mi_mdhhs.connector import (
    CAPABILITIES,
    COLUMNS,
    LANDING_URL,
    parse_city,
    parse_license,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    EntityKind,
    ExclusionAction,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import (
    Address,
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)
from tests.connectors.fixtures import builders, fixture_bytes, mi_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

FILE_URL = (
    "https://www.michigan.gov/mdhhs/-/media/Project/Websites/mdhhs/Assistance-Programs/"
    "Medicaid-BPHASA/MI-Sanctioned-Provider-List/MI_Sanctioned_Provider_List-XLSX.xlsx"
    "?rev=0123456789abcdef0123456789abcdef&hash=FEDCBA9876543210FEDCBA9876543210"
)
"""The workbook link of the landing fixture, made absolute and unescaped."""

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="mi_mdhhs",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=FILE_URL,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return MiMdhhsConnector(ScriptedFetcher()).parse(mi_builders.mi_mdhhs(), SNAPSHOT)


def _person(parsed: ParsedList, last_name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Last Name"] == last_name]
    return record


def _business(parsed: ParsedList, entity_name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Entity Name"] == entity_name]
    return record


def test_the_fixture_uses_the_connectors_columns() -> None:
    assert tuple(mi_builders.HEADER[:11]) == COLUMNS[:11]
    assert mi_builders.HEADER[11] == "Sanction Source"
    assert COLUMNS[11] == "Sanction Source (2)"


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 15
    assert parsed.skipped == 1
    assert parsed.merged == 1
    assert len(parsed.records) == 13
    assert parsed.unreadable == {"Sanction Date1": 1}
    assert parsed.note() == (
        "15 rows, 13 records, 1 skipped; 1 merged; unreadable values: Sanction Date1 1"
    )
    assert len({record.record_hash for record in parsed.records}) == 13


def test_a_home_help_worker_without_a_reason_is_a_sanction(parsed: ParsedList) -> None:
    record = _person(parsed, "Testperson")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery")
    assert record.action is ExclusionAction.SANCTION
    assert record.action_raw is None
    assert record.reason is None
    assert record.npi is None
    assert record.license is None
    assert record.address == Address(city="Mocktown")
    assert record.effective_date == date(2024, 3, 15)
    assert record.end_date is None
    assert record.raw["Provider Category"] == "Home Help"


def test_a_physician_with_npi_licence_and_later_sanctions(parsed: ParsedList) -> None:
    record = _person(parsed, "Sampledoc")

    assert record.person_name == PersonName(last="Sampledoc", first="Jordan", middle="Q.")
    assert record.npi == builders.NPI_A
    assert record.related_npis == ()
    assert record.license == License(number="4301000001", state="MI")
    assert record.effective_date == date(2019, 7, 1)
    assert record.action is ExclusionAction.TERMINATION
    assert record.action_raw == "Summary Suspension Terminated"
    assert record.reason == "Summary Suspension Terminated"
    assert record.raw["Sanction Date2"] == "8/1/2019  9/12/2020"
    assert record.raw["Sanction Source (2)"] == "MDHHS-OIG\nHHS-OIG"
    assert record.raw["Sanction Source"] == "MI-Licensing and Regulatory Affairs"


def test_a_suffix_a_padded_npi_and_another_states_city(parsed: ParsedList) -> None:
    record = _person(parsed, "Mockley JR.")

    assert record.person_name == PersonName(last="Mockley", first="Casey", suffix="JR.")
    assert record.npi == builders.NPI_B
    assert record.address == Address(city="Exampleton", state="OH")
    assert record.action is ExclusionAction.EXCLUSION


def test_a_former_surname_and_a_nickname_are_aliases(parsed: ParsedList) -> None:
    record = _person(parsed, "Sampleton (Priorton)")

    assert record.person_name == PersonName(last="Sampleton", first="Robin", middle="Lee")
    assert record.aliases == ("Priorton, Robin Lee", "Sampleton, Rob")
    assert record.license == License(number="2901000002", state="MI")
    assert record.action is ExclusionAction.SANCTION
    assert record.action_raw == "Voluntarily Surrendered"


def test_a_credential_after_a_comma_is_the_suffix(parsed: ParsedList) -> None:
    record = _person(parsed, "Testwell, MD")

    assert record.person_name == PersonName(last="Testwell", first="Morgan", suffix="MD")
    assert record.action is ExclusionAction.EXCLUSION


def test_a_business_alone_and_an_ncpdp_number_is_no_licence(parsed: ParsedList) -> None:
    record = _business(parsed, "Sampleville Pharmacy Inc")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sampleville Pharmacy Inc"
    assert record.person_name is None
    assert record.related_parties == ()
    assert record.npi == builders.NPI_C
    assert record.license is None
    assert record.raw["License#"] == "NCPDP 1234567"
    assert record.address == Address(city="Mockford")
    assert record.action is ExclusionAction.SUSPENSION


def test_a_business_listed_once_per_owner_is_one_record_with_both_owners(
    parsed: ParsedList,
) -> None:
    record = _business(parsed, "Example Care Pharmacy LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.related_parties == ("Ownermock, Dana", "Partnermock, Lee J")
    assert record.aliases == ()
    assert record.npi == builders.NPI_D
    assert record.license == License(number="5301000003", state="MI")
    assert record.raw["Last Name"] == "Ownermock; Partnermock"
    assert record.raw["First Name"] == "Dana; Lee"
    assert record.raw["Middle Name"] == "J"
    assert record.raw["Reason"] == "Suspended"
    assert record.raw["Sanction Date2"] == "5/1/2025"
    assert record.action is ExclusionAction.SUSPENSION


def test_owners_one_per_line_and_a_clia_number_is_no_licence(parsed: ParsedList) -> None:
    record = _business(parsed, "Mock Diagnostics Laboratory")

    assert record.related_parties == ("Firstowner, Alex", "Secondowner, Jamie")
    assert record.license is None
    assert record.effective_date == date(2021, 9, 14)
    assert record.action is ExclusionAction.EXCLUSION


def test_a_dba_in_the_business_name_is_an_alias(parsed: ParsedList) -> None:
    record = _business(parsed, "Sample Medical Group PC DBA Sample Family Clinic")

    assert record.organization_name == "Sample Medical Group PC"
    assert record.aliases == ("Sample Family Clinic",)
    assert record.license is None
    assert record.raw["License#"] == "43.012345"


def test_a_business_named_like_a_person_stays_a_business(parsed: ParsedList) -> None:
    record = _business(parsed, "Mockdent, Avery J., DDS, PC (AKA Mockdent Smiles PC)")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.person_name is None
    assert record.organization_name == "Mockdent, Avery J., DDS, PC"
    assert record.aliases == ("Mockdent Smiles PC",)
    assert record.action is ExclusionAction.TERMINATION


def test_two_first_names_on_a_business_row_are_two_related_names(parsed: ParsedList) -> None:
    record = _business(parsed, "Twin Mock Drugs")

    assert record.related_parties == ("Twinmock, Ann", "Twinmock, Lee")
    assert record.license == License(number="12345", state="MN")


def test_an_unreadable_date_is_counted_and_left_empty(parsed: ParsedList) -> None:
    record = _person(parsed, "Datemock")

    assert record.effective_date is None
    assert record.raw["Sanction Date1"] == "12/31/202"


def test_the_first_of_two_licences(parsed: ParsedList) -> None:
    record = _person(parsed, "Licmock")

    assert record.license == License(number="4704000004", state="MI")
    assert record.action is ExclusionAction.SUSPENSION


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("", None),
        ("N/A", None),
        ("4301000001", License(number="4301000001", state="MI")),
        ("12345 -MN", License(number="12345", state="MN")),
        ("12345-MN", License(number="12345", state="MN")),
        ("12345-67", License(number="12345-67", state="MI")),
        ("EMC1234567", License(number="EMC1234567", state="MI")),
        ("APRN.CNP.1234567", License(number="1234567", state="MI")),
        ("123456789 (CS)", License(number="123456789", state="MI")),
        ("4301000001  4301000002", License(number="4301000001", state="MI")),
        ("4301\u200b000001", License(number="4301000001", state="MI")),
        ("NCPDP 1234567", None),
        ("12D3456789", None),
        ("12D3456789 (CLIA only)", None),
        ("43.012345", None),
    ],
)
def test_licence_cells(cell: str, expected: License | None) -> None:
    assert parse_license(cell) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("", None),
        ("Mocktown", Address(city="Mocktown")),
        ("Mock Harbor, FL", Address(city="Mock Harbor", state="FL")),
        ("Mock Falls , VA", Address(city="Mock Falls", state="VA")),
        ("Mockville, Township", Address(city="Mockville, Township")),
    ],
)
def test_city_cells(cell: str, expected: Address | None) -> None:
    assert parse_city(cell) == expected


def test_a_workbook_without_the_header_fails() -> None:
    body = builders.workbook([("Something else",), ("A", "B")], title="Michigan")

    with pytest.raises(ParseError):
        MiMdhhsConnector(ScriptedFetcher()).parse(body, SNAPSHOT)


def test_capabilities_are_polite_link_discovery_with_the_browser_fallback() -> None:
    assert ConnectorCapabilities.model_validate(CAPABILITIES.model_dump()) == CAPABILITIES
    assert CAPABILITIES.source_id == "mi_mdhhs"
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.source_url == LANDING_URL
    assert LANDING_URL.startswith("https://")
    assert MiMdhhsConnector.landing_url == LANDING_URL
    assert CAPABILITIES.access_policy is AccessPolicy.HTTP
    assert CAPABILITIES.method is VerificationMethod.BULK_DATASET
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.browser_fallback is True
    assert CAPABILITIES.jurisdictions == frozenset({"MI"})
    assert CAPABILITIES.display_name == "Michigan MDHHS Sanctioned Provider List"
    assert CAPABILITIES.provider_type_fields == ("Provider Category",)
    assert MiMdhhsConnector(ScriptedFetcher(), browser=ScriptedFetcher()).uses_browser


def test_the_xlsx_link_is_found_next_to_the_pdf_with_its_query() -> None:
    page = fixture_bytes("mi_mdhhs_landing.html").decode()

    link = MiMdhhsConnector(ScriptedFetcher()).find_link(page)

    assert link == FILE_URL.removeprefix("https://www.michigan.gov")


def _page() -> FetchedDocument:
    return _document(fixture_bytes("mi_mdhhs_landing.html"), LANDING_URL, content_type="text/html")


def _document(
    body: bytes, url: str, *, content_type: str | None = None, in_browser: bool = False
) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None if in_browser else '"f0"',
        last_modified=None if in_browser else datetime(2026, 10, 30, tzinfo=UTC),
        content_type=content_type,
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=in_browser,
    )


_REFUSED = FetchFailure(outcome=Outcome.BLOCKED, status=403, note="HTTP 403", fetched_at=NOW)


@pytest.mark.anyio
async def test_a_new_link_is_downloaded_over_plain_http() -> None:
    http = ScriptedFetcher(_page(), _document(mi_builders.mi_mdhhs(), FILE_URL))
    browser = ScriptedFetcher()

    result = await MiMdhhsConnector(http, browser=browser).fetch(None)

    assert http.requests == [
        (LANDING_URL, ChangeSignal.CONTENT_HASH),
        (FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert browser.requests == []
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == FILE_URL
    assert result.snapshot.row_count == 13
    assert result.records is not None
    assert all(record.snapshot_id == result.snapshot.id for record in result.records)


@pytest.mark.anyio
async def test_a_refused_landing_page_and_file_are_read_in_the_browser() -> None:
    http = ScriptedFetcher(_REFUSED)
    workbook = _document(mi_builders.mi_mdhhs(), FILE_URL, in_browser=True)
    browser = ScriptedFetcher(
        _document(
            fixture_bytes("mi_mdhhs_landing.html"),
            LANDING_URL,
            content_type="text/html",
            in_browser=True,
        ),
        workbook,
    )

    result = await MiMdhhsConnector(http, browser=browser).fetch(None)

    assert browser.requests == [
        (LANDING_URL, ChangeSignal.CONTENT_HASH),
        (FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == FILE_URL
    assert result.snapshot.note == (
        "browser fallback; 15 rows, 13 records, 1 skipped; 1 merged; "
        "unreadable values: Sanction Date1 1"
    )
    assert result.records is not None


@pytest.mark.anyio
async def test_an_unchanged_link_is_one_request() -> None:
    previous = SNAPSHOT.model_copy(update={"row_count": 13, "etag": '"f0"'})
    http = ScriptedFetcher(_page())

    result = await MiMdhhsConnector(http, browser=ScriptedFetcher()).fetch(previous)

    assert http.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (link unchanged)"
    assert result.records is None


@pytest.mark.anyio
async def test_a_new_edition_query_is_a_new_link() -> None:
    previous = SNAPSHOT.model_copy(update={"source_url": FILE_URL.replace("0123", "9999", 1)})
    http = ScriptedFetcher(_page(), _document(mi_builders.mi_mdhhs(), FILE_URL))

    result = await MiMdhhsConnector(http).fetch(previous)

    assert len(http.requests) == 2
    assert result.snapshot.source_url == FILE_URL
    assert result.records is not None


@pytest.mark.anyio
async def test_a_challenge_the_browser_cannot_clear_stays_blocked() -> None:
    challenge = FetchFailure(
        outcome=Outcome.BLOCKED,
        status=403,
        note="challenge page not cleared (HTTP 403)",
        fetched_at=NOW,
    )

    result = await MiMdhhsConnector(
        ScriptedFetcher(_REFUSED), browser=ScriptedFetcher(challenge)
    ).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == (
        "HTTP 403; browser fallback: challenge page not cleared (HTTP 403)"
    )
    assert result.records is None


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.SOURCE_UNAVAILABLE, Outcome.FAILED])
async def test_a_failed_fetch_never_yields_records(outcome: Outcome) -> None:
    failure = FetchFailure(outcome=outcome, status=None, note="synthetic", fetched_at=NOW)

    result = await MiMdhhsConnector(ScriptedFetcher(failure)).fetch(None)

    assert result.snapshot.outcome is outcome
    assert result.records is None


@pytest.mark.anyio
async def test_a_page_without_the_link_is_failed_not_empty() -> None:
    page = _document(b"<html><body>Service notice</body></html>", LANDING_URL)

    result = await MiMdhhsConnector(ScriptedFetcher(page)).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.records is None
