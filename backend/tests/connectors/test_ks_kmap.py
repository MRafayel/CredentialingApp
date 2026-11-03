import hashlib
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.ks_kmap import KsKmapConnector
from credentialing.connectors.ks_kmap.connector import (
    CAPABILITIES,
    COLUMNS,
    first_kmap_number,
    parse_party,
    split_business_names,
)
from credentialing.domain.enums import ChangeSignal, EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import ks_builders
from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C, NPI_D, NPI_E, workbook
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id="ks_kmap",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url=KsKmapConnector.download_url,
    )
    return KsKmapConnector(ScriptedFetcher()).parse(ks_builders.ks_kmap(), snapshot)


def _person(parsed: ParsedList, last: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.person_name and r.person_name.last == last)


def _organization(parsed: ParsedList, name: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.organization_name == name)


def test_the_fixture_uses_the_connectors_columns() -> None:
    assert ks_builders.COLUMNS == COLUMNS


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 13
    assert parsed.skipped == 1
    assert parsed.merged == 1
    assert len(parsed.records) == 11
    assert parsed.unreadable == {"Termination Date:": 1}
    assert parsed.note() == (
        "13 rows, 11 records, 1 skipped; 1 merged; unreadable values: Termination Date: 1"
    )


def test_a_person_first_name_first(parsed: ParsedList) -> None:
    record = _person(parsed, "Testperson")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery", middle="J.")
    assert record.npi is None
    assert record.state_provider_id is None
    assert record.action is ExclusionAction.TERMINATION
    assert record.action_raw is None
    assert record.effective_date == date(2021, 3, 4)
    assert record.end_date is None
    assert record.reason == "HHS-OIG excluded participation."
    assert record.raw["Provider Type:"] == "Nurse/Nurse Aide"
    assert record.raw["KMAP Provider #:"] == "N/A"


def test_names_joined_by_a_slash_are_the_persons_other_names(parsed: ParsedList) -> None:
    record = _person(parsed, "Mockname")

    assert record.person_name == PersonName(last="Mockname", first="Jordan")
    assert record.aliases == ("Jordan Samplewed",)
    assert record.related_parties == ()


def test_an_organization_with_several_location_letters(parsed: ParsedList) -> None:
    record = _organization(parsed, "Sample Medical Lab, LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.person_name is None
    assert record.npi == NPI_A
    assert record.state_provider_id == "100000001A"
    assert record.raw["KMAP Provider #:"] == "100000001A,B,C"


def test_businesses_on_a_persons_row_are_related_parties(parsed: ParsedList) -> None:
    record = _person(parsed, "Exampleowner")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.aliases == ()
    assert record.related_parties == (
        "Sample Home Care, Inc.",
        "Sample Home Health Services",
        "Sample Associates, Inc.",
    )
    assert record.npi == NPI_B
    assert record.related_npis == (NPI_C,)
    assert record.state_provider_id == "100000002A"


def test_slash_separated_businesses_and_notes_in_identifier_cells(parsed: ParsedList) -> None:
    record = _person(parsed, "Sampleperson")

    assert record.person_name == PersonName(last="Sampleperson", first="Casey", middle="Q")
    assert record.related_parties == ("Mock & Test, Inc.", "Test of Kansas, Inc.")
    assert record.npi == NPI_D
    assert record.related_npis == ()
    assert record.state_provider_id == "100000004A"


def test_a_one_word_name_is_an_organization(parsed: ParsedList) -> None:
    record = _organization(parsed, "Samplelabs")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.npi == NPI_E


def test_a_generational_suffix_and_a_fourteen_digit_number(parsed: ParsedList) -> None:
    record = _person(parsed, "Fakename")

    assert record.person_name == PersonName(last="Fakename", first="Quinn", suffix="Jr.")
    assert record.state_provider_id == "12345678901234"


def test_a_credential_after_a_comma_and_a_text_date(parsed: ParsedList) -> None:
    record = _person(parsed, "Testdoc")

    assert record.person_name == PersonName(last="Testdoc", first="Morgan", suffix="MD")
    assert record.effective_date == date(2026, 10, 20)
    assert record.npi == ks_builders.NPI_G
    assert record.related_npis == ()


def test_a_dba_on_an_organizations_row_is_an_alias(parsed: ParsedList) -> None:
    record = _organization(parsed, "Sample Medical")

    assert record.aliases == ("Sample Healthcare", "Sample Clinic")
    assert record.related_parties == ()


def test_an_unreadable_date_is_left_empty(parsed: ParsedList) -> None:
    record = _person(parsed, "Mockperson")

    assert record.effective_date is None
    assert record.raw["Termination Date:"] == "2026-13-45"


def test_rows_repeating_an_identity_are_merged(parsed: ParsedList) -> None:
    record = _person(parsed, "Duplicate")

    assert record.raw["Provider Type:"] == "Counselor; Mental Health Provider"
    assert record.raw["Comments:"] == (
        "Loss of license or other State action.; Terminated under a separate Medicaid enrollment."
    )
    assert record.reason == "Loss of license or other State action."


def test_a_row_without_a_name_is_refused() -> None:
    with pytest.raises(ParseError):
        parse_party("  ", "Sample Clinic")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Sample Care LLC", ("Sample Care LLC",)),
        ("Sample Care, L.L.C.", ("Sample Care, L.L.C.",)),
        (
            "A Care, Inc., B Health Services, C Associates, Inc.",
            ("A Care, Inc.", "B Health Services", "C Associates, Inc."),
        ),
        ("A & B, Inc. / C of D,Inc.", ("A & B, Inc.", "C of D, Inc.")),
        ("A Care\nB Care; A Care", ("A Care", "B Care")),
        ("", ()),
        ("N/A", ()),
    ],
)
def test_split_business_names(cell: str, expected: tuple[str, ...]) -> None:
    assert split_business_names(cell) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("N/A", None),
        ("", None),
        ("100000001a", "100000001A"),
        ("100000001A,B,C,D", "100000001A"),
        ("100000001A (old);       100000002A", "100000001A"),
        ("100000001 100000002", "100000001"),
        ("12345678901234, 22345678901234", "12345678901234"),
        ("1234567", None),
    ],
)
def test_first_kmap_number(cell: str, expected: str | None) -> None:
    assert first_kmap_number(cell) == expected


def test_capabilities_poll_a_stable_file_slowly_with_the_browser_fallback() -> None:
    assert CAPABILITIES.source_id == "ks_kmap"
    assert CAPABILITIES.jurisdictions == frozenset({"KS"})
    assert CAPABILITIES.change_signal is ChangeSignal.CONTENT_HASH
    assert CAPABILITIES.download_url == KsKmapConnector.download_url
    assert KsKmapConnector.download_url.startswith("https://www.kdhe.ks.gov/DocumentCenter/")
    assert CAPABILITIES.source_url == "https://www.kdhe.ks.gov/172/Medicaid"
    assert CAPABILITIES.crawl_delay.total_seconds() == 10
    assert CAPABILITIES.poll_interval.days == 7
    assert CAPABILITIES.browser_fallback is True
    assert CAPABILITIES.browser_warm_up is True
    assert CAPABILITIES.last_modified_is_meaningful is False
    assert CAPABILITIES.provider_type_fields == ("Provider Type:",)
    assert KsKmapConnector(ScriptedFetcher(), browser=ScriptedFetcher()).uses_browser


def _document(
    body: bytes, *, in_browser: bool, last_modified: datetime | None = None
) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=last_modified,
        content_type=None,
        final_url=KsKmapConnector.download_url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=in_browser,
    )


_CHALLENGE = FetchFailure(
    outcome=Outcome.BLOCKED, status=403, note="challenge page (HTTP 403)", fetched_at=NOW
)


@pytest.mark.anyio
async def test_a_file_served_to_a_script_needs_no_browser() -> None:
    http = ScriptedFetcher(_document(ks_builders.ks_kmap(), in_browser=False))
    browser = ScriptedFetcher()

    result = await KsKmapConnector(http, browser=browser).fetch(None)

    assert http.requests == [(KsKmapConnector.download_url, ChangeSignal.CONTENT_HASH)]
    assert http.entry_urls == [None]
    assert browser.requests == []
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == KsKmapConnector.download_url
    assert result.records is not None
    assert len(result.records) == 11


@pytest.mark.anyio
async def test_a_cloudflare_challenge_is_repeated_in_the_browser() -> None:
    browser = ScriptedFetcher(_document(ks_builders.ks_kmap(), in_browser=True))

    result = await KsKmapConnector(ScriptedFetcher(_CHALLENGE), browser=browser).fetch(None)

    assert browser.requests == [(KsKmapConnector.download_url, ChangeSignal.CONTENT_HASH)]
    assert browser.entry_urls == ["https://www.kdhe.ks.gov/172/Medicaid"]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == (
        "browser fallback; 13 rows, 11 records, 1 skipped; 1 merged; "
        "unreadable values: Termination Date: 1"
    )
    assert result.records


@pytest.mark.anyio
async def test_the_request_time_last_modified_is_not_the_source_update_time() -> None:
    served = _document(
        ks_builders.ks_kmap(), in_browser=False, last_modified=datetime(2026, 11, 2, tzinfo=UTC)
    )

    result = await KsKmapConnector(ScriptedFetcher(served)).fetch(None)

    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.last_modified is None


@pytest.mark.anyio
async def test_a_block_in_the_browser_stays_blocked() -> None:
    attention = FetchFailure(
        outcome=Outcome.BLOCKED,
        status=403,
        note="challenge page not cleared (HTTP 403)",
        fetched_at=NOW,
    )

    result = await KsKmapConnector(
        ScriptedFetcher(_CHALLENGE), browser=ScriptedFetcher(attention)
    ).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == (
        "challenge page (HTTP 403); browser fallback: challenge page not cleared (HTTP 403)"
    )
    assert result.records is None


@pytest.mark.anyio
async def test_a_challenge_without_a_browser_is_blocked_not_empty() -> None:
    result = await KsKmapConnector(ScriptedFetcher(_CHALLENGE)).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.records is None


@pytest.mark.anyio
async def test_a_workbook_without_the_terminations_sheet_fails() -> None:
    other = workbook(ks_builders.ks_kmap_rows(), title="Sheet1")

    result = await KsKmapConnector(ScriptedFetcher(_document(other, in_browser=False))).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.note == "parse failed: the workbook has no sheet named 'Terminations'"
    assert result.records is None


@pytest.mark.anyio
async def test_an_html_page_instead_of_the_workbook_fails_not_empty() -> None:
    page = b"<html><head><title>Attention Required! | Cloudflare</title></head></html>"

    result = await KsKmapConnector(ScriptedFetcher(_document(page, in_browser=False))).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.records is None
