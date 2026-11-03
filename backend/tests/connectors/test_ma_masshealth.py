import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.ma_masshealth import MaMassHealthConnector
from credentialing.connectors.ma_masshealth.connector import (
    CAPABILITIES,
    LANDING_URL,
    is_organization_type,
    name_text,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    EntityKind,
    ExclusionAction,
    Outcome,
)
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from credentialing.domain.policies import record_has_ended
from tests.connectors.fixtures import builders, fixture_bytes, ma_masshealth_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

_FILE_URL = (
    "https://www.mass.gov/doc/list-of-suspended-or-excluded-masshealth-providers-8312026/download"
)
_SUFFIXED_URL = (
    "https://www.mass.gov/doc/list-of-suspended-or-excluded-masshealth-providers-8312026-0/download"
)

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="ma_masshealth",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=_FILE_URL,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    body = ma_masshealth_builders.ma_masshealth()
    return MaMassHealthConnector(ScriptedFetcher()).parse(body, SNAPSHOT)


def _record(parsed: ParsedList, name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Provider Name"] == name]
    return record


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 18
    assert parsed.skipped == 1
    assert parsed.merged == 2
    assert len(parsed.records) == 15
    assert parsed.unreadable == {"Suspension/Exclusion Effective Date": 1}
    assert parsed.note() == (
        "18 rows, 15 records, 1 skipped; 2 merged; "
        "unreadable values: Suspension/Exclusion Effective Date 1"
    )
    assert all(r.action is ExclusionAction.SANCTION for r in parsed.records)
    assert all(r.action_raw is None for r in parsed.records)
    assert all(r.snapshot_id == SNAPSHOT.id for r in parsed.records)


def test_a_person_with_an_npi_as_a_number(parsed: ParsedList) -> None:
    record = _record(parsed, "AVERY Q TESTPERSON")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="TESTPERSON", first="AVERY", middle="Q")
    assert record.npi == builders.NPI_A
    assert record.effective_date == date(2019, 5, 1)
    assert record.end_date is None
    assert record.reason == "DISCIPLINARY ACTION BY LICENSING BOARD"
    assert record.raw["Provider Type"] == "PHYSICIAN"
    assert record.raw["Suspension/Exclusion Effective Date"] == "2019-05-01"


def test_double_spaces_a_text_date_and_padded_cells(parsed: ParsedList) -> None:
    record = _record(parsed, "JORDAN  MOCKFIELD-SAMPLE")

    assert record.person_name == PersonName(last="MOCKFIELD-SAMPLE", first="JORDAN")
    assert record.npi == builders.NPI_B
    assert record.effective_date == date(2024, 3, 15)
    assert record.reason == "MEDICARE/MEDICAID DECERTIFICAITON"
    assert record.raw["Provider Type"] == "PHYSICIAN"


def test_a_personal_care_attendant_has_a_unique_id_and_no_npi(parsed: ParsedList) -> None:
    record = _record(parsed, "CASEY R SAMPLEWORTH")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.npi is None
    assert record.state_provider_id == "123456"


def test_a_comma_before_a_suffix_is_read_first_name_first(parsed: ParsedList) -> None:
    record = _record(parsed, "MORGAN T EXAMPLETON, II")

    assert record.person_name == PersonName(
        last="EXAMPLETON", first="MORGAN", middle="T", suffix="II"
    )
    assert record.npi is None
    assert record.raw["National Provider Identifier (NPI)"] == "NONE"


def test_a_business_name_with_a_comma_designator_is_an_organization(parsed: ParsedList) -> None:
    record = _record(parsed, "SAMPLE HOME HEALTH SERVICES, LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "SAMPLE HOME HEALTH SERVICES, LLC"
    assert record.person_name is None


def test_a_person_shaped_name_under_an_entity_type_is_an_organization(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "RILEY MOCKWOOD")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "RILEY MOCKWOOD"


def test_a_professional_corporation_is_its_person_with_the_practice_as_alias(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "DREW K TESTWELL, MD PC")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(
        last="TESTWELL", first="DREW", middle="K", suffix="MD PC"
    )
    assert record.aliases == ("DREW K TESTWELL MD PC",)


def test_a_parenthesized_part_of_a_business_name_is_an_alias(parsed: ParsedList) -> None:
    record = _record(parsed, "MOCKCO (SAMPLE HEALTH CARE SERVICES) LLC")

    assert record.organization_name == "MOCKCO LLC"
    assert record.aliases == ("SAMPLE HEALTH CARE SERVICES",)


def test_rows_differing_only_in_provider_type_are_one_record(parsed: ParsedList) -> None:
    [record] = [r for r in parsed.records if r.npi == builders.NPI_C]

    assert record.organization_name == "SAMPLECARE AGENCY INC"
    assert record.raw["Provider Type"] == (
        "SPECIAL PROGRAMS; HOME HEALTH AGENCY; ADULT FOSTER CARE / GROUP ADULT FOSTER CARE"
    )
    assert record.raw["Suspension/Exclusion Reason"] == (
        "VIOLATION OF MASSHEALTH PROGRAM PARTICIPATORY REQUIREMENTS"
    )


def test_rows_with_another_npi_stay_separate(parsed: ParsedList) -> None:
    records = [r for r in parsed.records if r.raw["Provider Name"] == "TAYLOR B MOCKSON"]

    assert sorted(r.npi or "" for r in records) == sorted([builders.NPI_D, builders.NPI_E])


def test_an_unreadable_date_and_an_invalid_npi_are_left_empty(parsed: ParsedList) -> None:
    record = _record(parsed, "QUINN SAMPLEFORD")

    assert record.effective_date is None
    assert record.npi is None
    assert record.raw["National Provider Identifier (NPI)"] == builders.INVALID_TEN_DIGITS


def test_a_one_word_name_is_a_person_with_a_last_name_only(parsed: ParsedList) -> None:
    record = _record(parsed, "TESTONYMOUS")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="TESTONYMOUS", first="")


def test_reinstated_providers_are_records_that_have_ended(parsed: ParsedList) -> None:
    person = _record(parsed, "ALEX  SAMPLEMAN")
    business = _record(parsed, "SAMPLE VISION CENTER, INC.")

    assert person.person_name == PersonName(last="SAMPLEMAN", first="ALEX")
    assert person.npi == builders.NPI_F
    assert (person.effective_date, person.end_date) == (date(2018, 1, 2), date(2022, 6, 1))
    assert business.entity_kind is EntityKind.ORGANIZATION
    assert business.npi == ma_masshealth_builders.NPI_G
    assert business.reason == "MEDICARE SUSPENSION"
    assert business.raw["Notes"] == "Reinstated after a made-up review"
    assert business.raw["Provider Type"] == "GROUP PRACTICE ORGANIZATION"
    assert record_has_ended(person, date(2026, 11, 2))
    assert not record_has_ended(_record(parsed, "AVERY Q TESTPERSON"), date(2026, 11, 2))


def test_a_workbook_without_the_reinstated_sheet_is_rejected() -> None:
    body = ma_masshealth_builders.ma_masshealth(reinstated=None)

    with pytest.raises(ParseError, match="no sheet 1"):
        MaMassHealthConnector(ScriptedFetcher()).parse(body, SNAPSHOT)


def test_a_changed_header_is_rejected() -> None:
    renamed = [
        ("Name", *ma_masshealth_builders.LISTED_HEADER[1:]),
        *ma_masshealth_builders.LISTED[1:],
    ]
    body = builders.workbook(
        ma_masshealth_builders.REINSTATED, first_sheet=("Exclusions List", renamed)
    )

    with pytest.raises(ParseError, match="no header row"):
        MaMassHealthConnector(ScriptedFetcher()).parse(body, SNAPSHOT)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("AVERY Q SAMPLE, II", "AVERY Q SAMPLE II"),
        ("AVERY SAMPLE, MD PC", "AVERY SAMPLE MD PC"),
        ("AVERY SAMPLE,DMD.", "AVERY SAMPLE DMD."),
        ("SAMPLE HEALTH, LLC", "SAMPLE HEALTH, LLC"),
        ("SAMPLE CARE, INC.", "SAMPLE CARE, INC."),
        ("SAMPLE, AVERY", "SAMPLE, AVERY"),
        (", MD", ", MD"),
        ("AVERY SAMPLE", "AVERY SAMPLE"),
    ],
)
def test_name_text(cell: str, expected: str) -> None:
    assert name_text(cell) == expected


@pytest.mark.parametrize(
    ("provider_type", "expected"),
    [
        ("GROUP PRACTICE ORGANIZATION", True),
        (" HOME HEALTH AGENCY", True),
        ("ADULT FOSTER CARE /  GROUP ADULT FOSTER CARE", True),
        ("Pharmacy", True),
        ("PHYSICIAN", False),
        ("PERSONAL CARE ATTENDANT", False),
        ("", False),
    ],
)
def test_organization_types(provider_type: str, expected: bool) -> None:
    assert is_organization_type(provider_type) is expected


def test_capabilities() -> None:
    assert CAPABILITIES.source_id == "ma_masshealth"
    assert CAPABILITIES.jurisdictions == frozenset({"MA"})
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.source_url == LANDING_URL
    assert CAPABILITIES.source_url.startswith("https://")
    assert MaMassHealthConnector.landing_url == LANDING_URL
    assert CAPABILITIES.display_name == "List of Suspended or Excluded MassHealth Providers"
    assert CAPABILITIES.provider_type_fields == ("Provider Type",)
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.access_policy is AccessPolicy.HTTP
    assert CAPABILITIES.browser_fallback is True
    assert MaMassHealthConnector(ScriptedFetcher(), browser=ScriptedFetcher()).uses_browser


def test_the_workbook_link_is_found_by_its_label_not_the_pdf() -> None:
    page = fixture_bytes("ma_masshealth_landing.html").decode()

    assert MaMassHealthConnector(ScriptedFetcher()).find_link(page) == _FILE_URL


def test_the_workbook_link_is_found_when_it_has_the_suffixed_slug() -> None:
    page = (
        fixture_bytes("ma_masshealth_landing.html")
        .decode()
        .replace("8312026-0/download", "8312026-X/download")
        .replace("8312026/download", "8312026-0/download")
        .replace("8312026-X/download", "8312026/download")
    )

    link = MaMassHealthConnector(ScriptedFetcher()).find_link(page)

    assert link == _SUFFIXED_URL


def test_a_page_without_the_workbook_link_raises() -> None:
    page = fixture_bytes("ma_masshealth_landing.html").decode().replace("XLSX", "DOCX")

    with pytest.raises(ParseError):
        MaMassHealthConnector(ScriptedFetcher()).find_link(page)


def _document(body: bytes, url: str, *, in_browser: bool = False) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag='"synthetic=="',
        last_modified=datetime(2026, 10, 27, tzinfo=UTC),
        content_type="text/html; charset=UTF-8",
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=in_browser,
    )


_BLOCKED = FetchFailure(outcome=Outcome.BLOCKED, status=403, note="HTTP 403", fetched_at=NOW)


@pytest.mark.anyio
async def test_a_refused_landing_page_is_read_in_the_browser_and_the_file_over_http() -> None:
    landing = _document(fixture_bytes("ma_masshealth_landing.html"), LANDING_URL, in_browser=True)
    workbook = _document(ma_masshealth_builders.ma_masshealth(), _FILE_URL)
    http = ScriptedFetcher(_BLOCKED, workbook)
    browser = ScriptedFetcher(landing)

    result = await MaMassHealthConnector(http, browser=browser).fetch(None)

    assert http.requests == [
        (LANDING_URL, ChangeSignal.CONTENT_HASH),
        (_FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert browser.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == _FILE_URL
    assert (result.snapshot.note or "").startswith("browser fallback; 18 rows, 15 records")
    assert result.records is not None
    assert len(result.records) == 15
    assert result.snapshot.row_count == len(result.records)
    for record in result.records:
        assert record.snapshot_id == result.snapshot.id
        assert set(CAPABILITIES.provider_type_fields) <= set(record.raw)
        assert record.npi not in record.related_npis


@pytest.mark.anyio
async def test_a_refused_download_is_repeated_in_the_browser() -> None:
    landing = _document(fixture_bytes("ma_masshealth_landing.html"), LANDING_URL)
    workbook = _document(ma_masshealth_builders.ma_masshealth(), _FILE_URL, in_browser=True)
    http = ScriptedFetcher(landing, _BLOCKED)
    browser = ScriptedFetcher(workbook)

    result = await MaMassHealthConnector(http, browser=browser).fetch(None)

    assert browser.requests == [(_FILE_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert (result.snapshot.note or "").startswith("browser fallback; ")


@pytest.mark.anyio
async def test_an_unchanged_link_is_one_request() -> None:
    previous = SNAPSHOT.model_copy(
        update={"content_hash": "ab" * 32, "etag": '"synthetic=="', "row_count": 15}
    )
    http = ScriptedFetcher(_document(fixture_bytes("ma_masshealth_landing.html"), LANDING_URL))

    result = await MaMassHealthConnector(http, browser=ScriptedFetcher()).fetch(previous)

    assert http.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (link unchanged)"
    assert result.records is None


@pytest.mark.anyio
async def test_a_page_blocked_in_the_browser_too_is_blocked_not_empty() -> None:
    challenge = replace(_BLOCKED, note="challenge page not cleared (HTTP 403)")
    connector = MaMassHealthConnector(ScriptedFetcher(_BLOCKED), browser=ScriptedFetcher(challenge))

    result = await connector.fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == (
        "HTTP 403; browser fallback: challenge page not cleared (HTTP 403)"
    )
    assert result.records is None
