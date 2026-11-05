import hashlib
from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.ak_dhss import AkDhssConnector
from credentialing.connectors.ak_dhss.connector import (
    CAPABILITIES,
    COLUMNS,
    LANDING_URL,
    REINSTATED,
    parse_party,
    parse_reason,
)
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.connectors.framework.rows import ParsedList
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    EntityKind,
    ExclusionAction,
    Outcome,
)
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import ak_builders
from tests.connectors.fixtures.pdf_builders import build_table_pdf
from tests.fakes import BrowserOptions, ScriptedFetcher
from tests.synthetic import NOW

FILE_URL = "https://health.alaska.gov/media/abc123xy/exclusion-list-august-2026.pdf"

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="ak_dhss",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=FILE_URL,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return AkDhssConnector(ScriptedFetcher()).parse(ak_builders.ak_dhss(), SNAPSHOT)


def _record(parsed: ParsedList, last_name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["LAST NAME"] == last_name]
    return record


def _row(last: str, first: str, authority: str = "OIG", reason: str = "") -> dict[str, str]:
    return dict(zip(COLUMNS, ["1/2/2020", last, first, "", authority, reason], strict=True))


def test_counts_without_the_footer_note(parsed: ParsedList) -> None:
    assert parsed.rows == 13
    assert parsed.skipped == 0
    assert parsed.merged == 0
    assert len(parsed.records) == 13
    assert parsed.unreadable == {}
    assert not any(r.raw["EXCLUSION DATE"].startswith("NOTE") for r in parsed.records)
    assert all(r.action is ExclusionAction.EXCLUSION for r in parsed.records)


def test_a_person_row_reads_its_name_date_and_reason(parsed: ParsedList) -> None:
    record = _record(parsed, "TESTPERSON")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="TESTPERSON", first="AVERY", middle="Q.")
    assert record.effective_date == date(2019, 1, 15)
    assert record.reason == "SSA Section 1128(a)(1) (OIG)"
    assert record.action_raw is None
    assert record.aliases == ()
    assert (record.npi, record.license, record.address, record.end_date) == (
        None,
        None,
        None,
        None,
    )
    assert record.source_id == "ak_dhss"
    assert record.valid_from == NOW


def test_a_generational_suffix_comes_off_the_last_name(parsed: ParsedList) -> None:
    record = _record(parsed, "SAMPLEMAN, JR")

    assert record.person_name == PersonName(last="SAMPLEMAN", first="JORDAN", suffix="JR")


def test_parenthesized_names_are_aliases_from_either_name_column(parsed: ParsedList) -> None:
    both = _record(parsed, "MOCKWELL (aka Priormock)")
    surname = _record(parsed, "EXAMPLETON (FORMERMOCK)")
    given = _record(parsed, "QUINNMOCK")

    assert both.person_name == PersonName(last="MOCKWELL", first="RILEY")
    assert both.aliases == ("Priormock, RILEY", "MOCKWELL, Ree")
    assert surname.person_name == PersonName(last="EXAMPLETON", first="TATUM", middle="RAE")
    assert surname.aliases == ("FORMERMOCK, TATUM RAE",)
    assert given.person_name == PersonName(last="QUINNMOCK", first="BO")
    assert given.aliases == ("QUINNMOCK, Bodie Lark",)


def test_the_asterisk_marks_a_reinstated_party_that_stays_listed(parsed: ParsedList) -> None:
    record = _record(parsed, "*REINSTATEDMOCK")

    assert record.person_name == PersonName(last="REINSTATEDMOCK", first="MORGAN")
    assert record.action_raw == REINSTATED == "Reinstated (*)"
    assert record.action is ExclusionAction.EXCLUSION
    assert record.end_date is None


def test_a_dash_first_name_is_an_organization_named_as_written(parsed: ParsedList) -> None:
    organizations = {
        r.organization_name: r.aliases
        for r in parsed.records
        if r.entity_kind is EntityKind.ORGANIZATION
    }

    assert organizations == {
        "SAMPLE HOME CARE, LLC": (),
        "417, LLC": (),
        "MOCK CARE SERVICES LLC": (),
        "SAMPLE DENTAL, LLC": ("SMILE MOCK",),
    }
    assert all(r.person_name is None for r in parsed.records if r.organization_name)


def test_reason_and_authority_are_composed_and_dashes_are_empty(parsed: ParsedList) -> None:
    wrapped = _record(parsed, "MOCKWELL (aka Priormock)")
    authority_only = _record(parsed, "MOCK CARE\nSERVICES LLC")
    neither = _record(parsed, "TESTWELL")

    assert wrapped.reason == "STATE SANCTION REASONS (MFCU)"
    assert authority_only.reason == "STATE"
    assert neither.reason is None
    assert neither.raw["PROVIDER TYPE"] == "-"


def test_a_leading_credential_in_the_first_name_is_kept_as_written(parsed: ParsedList) -> None:
    assert _record(parsed, "TESTWELL").person_name == PersonName(
        last="TESTWELL", first="DO", middle="AVERY"
    )


def test_a_beneficiary_row_is_kept(parsed: ParsedList) -> None:
    record = _record(parsed, "BENEFICIARYMOCK")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.raw["PROVIDER TYPE"] == "RECIPIENT/BENEFICIARY"


def test_a_row_drawn_as_one_cell_is_read_into_its_columns(parsed: ParsedList) -> None:
    record = _record(parsed, "SPANMOCK")

    assert record.person_name == PersonName(last="SPANMOCK", first="CASEY")
    assert record.effective_date == date(2010, 2, 10)
    assert record.raw["PROVIDER TYPE"] == "HEALTH CARE AIDE"
    assert record.reason == "SSA Section 1128(a)(1) (OIG)"


def test_a_row_in_one_cell_that_does_not_split_is_counted_as_skipped() -> None:
    crossing = ["8/8/2018 TESTPERSON AVERY RUNNING INTO THE NEXT COLUMNS", "", "", "", "", ""]
    body = build_table_pdf(
        [
            [
                ak_builders.HEADER,
                ["1/2/2020", "SAMPLETON", "JORDAN", "HEALTH CARE AIDE", "OIG", ""],
                crossing,
            ]
        ],
        column_widths=ak_builders.COLUMN_WIDTHS,
        spanning_rows={(0, 2)},
    )

    result = AkDhssConnector(ScriptedFetcher()).parse(body, SNAPSHOT)

    assert (result.rows, len(result.records), result.skipped) == (2, 1, 1)


def test_a_row_without_a_last_name_is_rejected() -> None:
    with pytest.raises(ParseError, match="no last name"):
        parse_party(_row("", "AVERY"))
    with pytest.raises(ParseError, match="no last name"):
        parse_party(_row("*", "AVERY"))


def test_an_empty_first_name_is_an_organization_too() -> None:
    party = parse_party(_row("SAMPLE TRANSPORT CO", ""))

    assert (party.person, party.organization) == (None, "SAMPLE TRANSPORT CO")


@pytest.mark.parametrize(
    ("authority", "reason", "expected"),
    [
        ("OIG", "SSA Section 1128(b)(4)", "SSA Section 1128(b)(4) (OIG)"),
        ("", "CONVICTION", "CONVICTION"),
        ("-", "CONVICTION", "CONVICTION"),
        ("PI", "-", "PI"),
        ("", "", None),
    ],
)
def test_the_reason_names_its_authority(authority: str, reason: str, expected: str | None) -> None:
    assert parse_reason(_row("TESTPERSON", "AVERY", authority, reason)) == expected


def test_the_list_link_is_found_on_the_landing_page() -> None:
    page = ak_builders.ak_dhss_landing().decode()

    assert AkDhssConnector(ScriptedFetcher()).find_link(page) == ak_builders.FILE_PATH


def test_a_landing_page_without_the_list_link_is_a_parse_error() -> None:
    page = '<a href="/media/abc123xy/annual-report-2026.pdf">Report</a>'

    with pytest.raises(ParseError):
        AkDhssConnector(ScriptedFetcher()).find_link(page)


def test_capabilities_discover_the_link_through_the_tuned_browser() -> None:
    assert CAPABILITIES.source_id == "ak_dhss"
    assert CAPABILITIES.jurisdictions == frozenset({"AK"})
    assert CAPABILITIES.access_policy is AccessPolicy.BROWSER
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.source_url == LANDING_URL == AkDhssConnector.landing_url
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.provider_type_fields == ("PROVIDER TYPE",)
    assert CAPABILITIES.browser_fallback is True
    assert CAPABILITIES.browser_warm_up is True
    assert CAPABILITIES.browser_webdriver_disabled is True
    assert CAPABILITIES.browser_challenge_timeout == timedelta(seconds=150)
    assert AkDhssConnector(ScriptedFetcher(), browser=ScriptedFetcher()).uses_browser


def _in_browser(body: bytes, url: str, content_type: str) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=None,
        content_type=content_type,
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=True,
    )


_CHALLENGE = FetchFailure(
    outcome=Outcome.BLOCKED, status=403, note="challenge page (HTTP 403)", fetched_at=NOW
)


@pytest.mark.anyio
async def test_page_and_file_go_straight_to_the_tuned_browser_without_a_plain_request() -> None:
    http = ScriptedFetcher()  # fails if fetched: no plain request is sent
    browser = ScriptedFetcher(
        _in_browser(ak_builders.ak_dhss_landing(), LANDING_URL, "text/html; charset=utf-8"),
        _in_browser(ak_builders.ak_dhss(), FILE_URL, "application/pdf"),
    )

    result = await AkDhssConnector(http, browser=browser).fetch(None)

    assert http.requests == []
    assert [url for url, _ in browser.requests] == [LANDING_URL, FILE_URL]
    assert browser.entry_urls == [LANDING_URL, LANDING_URL]
    assert (
        browser.browser_options
        == [
            BrowserOptions("ak_dhss", True, timedelta(seconds=150)),
        ]
        * 2
    )
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == FILE_URL
    assert result.snapshot.note == "browser fallback; 13 rows, 13 records, 0 skipped"
    assert result.records is not None
    assert len(result.records) == 13


@pytest.mark.anyio
async def test_without_a_browser_the_plain_request_is_sent_and_stays_blocked() -> None:
    http = ScriptedFetcher(_CHALLENGE)

    result = await AkDhssConnector(http).fetch(None)

    assert [url for url, _ in http.requests] == [LANDING_URL]
    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.records is None
