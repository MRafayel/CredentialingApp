import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, Response
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.il_hfs import IlHfsConnector
from credentialing.connectors.il_hfs.connector import (
    CAPABILITIES,
    COLUMNS,
    SOURCE_URL,
    TOKEN_URL,
    parse_party,
    parse_stamp,
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
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)
from tests.connectors.fixtures import builders, il_builders
from tests.fakes import SESSION_OPENED_AT, ScriptedFetcher
from tests.synthetic import NOW

VERSION = f"{il_builders.STAMP}|{il_builders.COUNT}"

SNAPSHOT = SourceSnapshot(
    id=uuid4(), source_id="il_hfs", fetched_at=NOW, outcome=Outcome.COMPLETED,
    source_url=il_builders.EXPORT_URL,
)  # fmt: skip


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return IlHfsConnector(ScriptedFetcher()).parse(il_builders.il_hfs(), SNAPSHOT)


def _record(parsed: ParsedList, name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Provider Name"] == name]
    return record


def _page(body: bytes | None = None) -> FetchedDocument:
    page = body if body is not None else il_builders.il_hfs_page()
    return FetchedDocument(
        body=page,
        content_hash=hashlib.sha256(page).hexdigest(),
        etag=None,
        last_modified=None,
        content_type="text/html; charset=utf-8",
        final_url=SOURCE_URL,
        byte_size=len(page),
        fetched_at=NOW,
        status=200,
    )


def _previous(etag: str | None = VERSION) -> SourceSnapshot:
    return SNAPSHOT.model_copy(update={"etag": etag, "content_hash": "ab" * 32})


# --- the page --------------------------------------------------------------------------------


def test_the_fixture_uses_the_connectors_columns() -> None:
    assert il_builders.COLUMNS == COLUMNS


def test_read_metadata_gives_the_edition_the_list_date_and_the_export() -> None:
    metadata = IlHfsConnector(ScriptedFetcher()).read_metadata(il_builders.il_hfs_page())

    assert metadata.url == il_builders.EXPORT_URL
    assert metadata.hash is None
    assert metadata.version == "10/13/2026 12:34:33 PM|3273"
    assert metadata.last_modified == datetime(2026, 10, 13, 17, 34, 33, tzinfo=UTC)
    assert metadata.parameters["viewName"] == "Active Sanctions"
    assert metadata.parameters["sortExpression"] == "mock_0 ASC"
    assert metadata.parameters["base64SecureConfiguration"] == il_builders.SECURE_CONFIGURATION
    columns = json.loads(metadata.parameters["columns"])
    assert [column["Name"] for column in columns] == list(COLUMNS)


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("10/13/2026 12:34:33 PM", datetime(2026, 10, 13, 17, 34, 33, tzinfo=UTC)),
        ("1/5/2026 9:02:03 AM", datetime(2026, 1, 5, 15, 2, 3, tzinfo=UTC)),
    ],
)
def test_the_stamp_is_central_time_converted_to_utc(stamp: str, expected: datetime) -> None:
    assert parse_stamp(stamp) == expected


@pytest.mark.parametrize(
    ("page", "message"),
    [
        (il_builders.il_hfs_page(stamp=None), "Sanctions Last Updated"),
        (il_builders.il_hfs_page(count=None), "Active Sanctions Count"),
        (il_builders.il_hfs_page(stamp="13/45/2026 12:34:33 PM"), "not a date"),
        (
            il_builders.il_hfs_page(layouts=(il_builders.layout("Recent Additions"),)),
            "no 'Active Sanctions' view",
        ),
        (
            il_builders.il_hfs_page(
                layouts=(il_builders.layout("Active Sanctions", export_type=7),)
            ),
            "no export action",
        ),
        (b"<strong>Sanctions Last Updated: 10/13/2026 12:34:33 PM</strong> "
         b"Active Sanctions Count: 3", "no grid view layouts"),
        (b"Sanctions Last Updated: 10/13/2026 12:34:33 PM Active Sanctions Count: 3 "
         b"data-view-layouts='bm90IGpzb24='", "unreadable"),
    ],
)  # fmt: skip
def test_an_unexpected_page_raises(page: bytes, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        IlHfsConnector(ScriptedFetcher()).read_metadata(page)


# --- fetch: one request when unchanged, four when changed ---------------------------------


@pytest.mark.anyio
async def test_an_unchanged_edition_is_one_request() -> None:
    fetcher = ScriptedFetcher(_page())

    result = await IlHfsConnector(fetcher).fetch(_previous())

    assert fetcher.requests == [(SOURCE_URL, ChangeSignal.CONTENT_HASH)]
    assert fetcher.sessions == []
    assert result.records is None
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (metadata unchanged)"
    assert result.snapshot.etag == VERSION
    assert result.snapshot.last_modified == datetime(2026, 10, 13, 17, 34, 33, tzinfo=UTC)
    assert result.snapshot.source_url == il_builders.EXPORT_URL


@pytest.mark.anyio
@pytest.mark.parametrize(
    "previous",
    [None, "10/13/2026 12:34:33 PM|3272", "10/06/2026 09:00:00 AM|3273"],
)
async def test_a_new_edition_is_exported_through_the_token_flow(previous: str | None) -> None:
    body = il_builders.il_hfs()
    fetcher = ScriptedFetcher(_page(), session_answers=il_builders.export_answers(body))

    result = await IlHfsConnector(fetcher).fetch(_previous(previous) if previous else None)

    assert fetcher.requests == [(SOURCE_URL, ChangeSignal.CONTENT_HASH)]
    assert fetcher.crawl_delays == [timedelta(seconds=10)]
    assert [session.closed for session in fetcher.sessions] == [True]
    token, export, download = fetcher.session_requests
    assert (token.method, token.url) == ("GET", TOKEN_URL)
    assert token.headers == {"X-Requested-With": "XMLHttpRequest", "Referer": SOURCE_URL}
    assert (export.method, export.url) == ("POST", il_builders.EXPORT_URL)
    assert export.headers == {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": SOURCE_URL,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json; charset=UTF-8",
        "__RequestVerificationToken": il_builders.ANTI_FORGERY_VALUE,
    }
    assert isinstance(export.json, dict)
    assert list(export.json) == [
        "viewName", "columns", "base64SecureConfiguration", "sortExpression", "search",
        "filter", "metaFilter", "nlSearchFilter", "page", "pageSize", "timezoneOffset",
        "isDSTObserved",
    ]  # fmt: skip
    assert export.json["viewName"] == "Active Sanctions"
    assert export.json["base64SecureConfiguration"] == il_builders.SECURE_CONFIGURATION
    assert export.json["sortExpression"] == "mock_0 ASC"
    assert export.json["columns"] == il_builders.layout("Active Sanctions")["Columns"]
    assert (export.json["page"], export.json["pageSize"]) == (1, 100)
    assert export.json["search"] is None
    assert export.json["timezoneOffset"] == 0
    assert export.json["isDSTObserved"] is False
    assert (download.method, download.url) == ("GET", il_builders.EXPORT_URL)
    assert download.params == {"key": il_builders.SESSION_KEY}
    assert download.headers == {"Referer": SOURCE_URL}
    snapshot = result.snapshot
    assert snapshot.outcome is Outcome.COMPLETED
    assert snapshot.source_url == il_builders.EXPORT_URL
    assert snapshot.etag == VERSION
    assert snapshot.last_modified == datetime(2026, 10, 13, 17, 34, 33, tzinfo=UTC)
    assert snapshot.fetched_at == SESSION_OPENED_AT
    assert snapshot.content_hash == hashlib.sha256(body).hexdigest()
    assert snapshot.byte_size == len(body)
    assert snapshot.note == (
        "15 rows, 13 records, 1 skipped; 1 merged; unreadable values: Action Date 1"
    )
    assert result.records is not None
    assert len(result.records) == 13


@pytest.mark.anyio
async def test_the_secure_configuration_and_the_token_never_reach_the_snapshot() -> None:
    body = il_builders.il_hfs()
    fetcher = ScriptedFetcher(_page(), session_answers=il_builders.export_answers(body))

    result = await IlHfsConnector(fetcher).fetch(None)

    stored = result.snapshot.model_dump_json()
    for secret in (
        il_builders.SECURE_CONFIGURATION,
        il_builders.ANTI_FORGERY_VALUE,
        "key=",
        "sessionKey",
    ):
        assert secret not in stored


_BLOCKED = FetchFailure(outcome=Outcome.BLOCKED, status=403, note="HTTP 403", fetched_at=NOW)
_DOWN = FetchFailure(
    outcome=Outcome.SOURCE_UNAVAILABLE, status=500, note="HTTP 500", fetched_at=NOW
)
_TOKEN_PAGE = il_builders.answer(il_builders.token_html())
_ACCEPTED = il_builders.export_answers(b"")[1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answers", "outcome", "note"),
    [
        ((_BLOCKED,), Outcome.BLOCKED, "anti-forgery token: HTTP 403"),
        ((il_builders.answer(b"<html>maintenance</html>"),), Outcome.FAILED,
         "no anti-forgery token"),
        ((_TOKEN_PAGE, _DOWN), Outcome.SOURCE_UNAVAILABLE, "export request: HTTP 500"),
        ((_TOKEN_PAGE, il_builders.answer(b'{"success":false}')), Outcome.FAILED,
         "export not accepted (HTTP 200)"),
        ((_TOKEN_PAGE, il_builders.answer(b"<html>error</html>")), Outcome.FAILED,
         "export not accepted (HTTP 200)"),
        ((_TOKEN_PAGE, il_builders.answer(b'{"success":true,"sessionKey":""}')),
         Outcome.FAILED, "export not accepted (HTTP 200)"),
        ((_TOKEN_PAGE, _ACCEPTED, _BLOCKED), Outcome.BLOCKED,
         "export download: HTTP 403"),
        ((_TOKEN_PAGE, _ACCEPTED, il_builders.answer(b"<html>session expired</html>")),
         Outcome.FAILED, "export download is not a workbook"),
        ((_TOKEN_PAGE, _ACCEPTED, il_builders.answer(il_builders.il_hfs(), content_type=None)),
         Outcome.FAILED, "export download is not a workbook"),
    ],
)  # fmt: skip
async def test_every_failed_step_is_an_outcome_without_records(
    answers: tuple[Response | FetchFailure, ...], outcome: Outcome, note: str
) -> None:
    fetcher = ScriptedFetcher(_page(), session_answers=answers)

    result = await IlHfsConnector(fetcher).fetch(_previous("an older edition"))

    assert result.snapshot.outcome is outcome
    assert result.snapshot.note == note
    assert result.snapshot.etag is None
    assert result.snapshot.content_hash is None
    assert result.records is None
    assert len(fetcher.session_requests) == len(answers)
    assert [session.closed for session in fetcher.sessions] == [True]


@pytest.mark.anyio
async def test_a_blocked_page_is_one_request_and_no_session() -> None:
    fetcher = ScriptedFetcher(_BLOCKED)

    result = await IlHfsConnector(fetcher).fetch(_previous())

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert fetcher.sessions == []
    assert result.records is None


@pytest.mark.anyio
async def test_an_unreadable_page_is_failed_not_empty() -> None:
    fetcher = ScriptedFetcher(_page(b"<html>Service notice</html>"))

    result = await IlHfsConnector(fetcher).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.note == (
        "metadata unreadable: no 'Sanctions Last Updated' stamp on the page"
    )
    assert fetcher.sessions == []


@pytest.mark.anyio
async def test_a_workbook_that_cannot_be_parsed_is_failed() -> None:
    answers = il_builders.export_answers(b"PK not really a workbook")
    fetcher = ScriptedFetcher(_page(), session_answers=answers)

    result = await IlHfsConnector(fetcher).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert result.snapshot.note == "parse failed: not a readable xlsx workbook (BadZipFile)"
    assert result.snapshot.etag is None
    assert result.records is None


# --- the rows ---------------------------------------------------------------------------------


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 15
    assert parsed.skipped == 1
    assert parsed.merged == 1
    assert len(parsed.records) == 13
    assert parsed.unreadable == {"Action Date": 1}


def test_a_surname_first_person_with_licence_npi_and_address(parsed: ParsedList) -> None:
    record = _record(parsed, "TESTPERSON AVERY Q")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="TESTPERSON", first="AVERY", middle="Q")
    assert record.license == License(number="036123456", state="IL")
    assert record.npi == builders.NPI_A
    assert record.action is ExclusionAction.TERMINATION
    assert record.action_raw == "TERMINATED"
    assert record.effective_date == date(2019, 3, 4)
    assert record.end_date is None
    assert record.address == Address(
        line1="100 SAMPLE ST", city="CHICAGO", state="IL", postal_code="60601"
    )
    assert record.related_parties == ()
    assert record.raw["Provider Type"] == "PHYSICIAN"


def test_a_credential_inside_the_name_becomes_the_suffix(parsed: ParsedList) -> None:
    record = _record(parsed, "MOCKDOC M.D. JORDAN")

    assert record.person_name == PersonName(last="MOCKDOC", first="JORDAN", suffix="M.D.")
    assert record.license == License(number="125.012345", state="IL")
    assert record.action is ExclusionAction.OTHER
    assert record.action_raw == "VOLUNTARY WITHDRAWAL"
    assert record.address == Address(
        line1="2 EXAMPLE AVE", line2="STE 300", city="PEORIA", state="IL",
        postal_code="61602-1234",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("name", "person"),
    [
        ("SAMPLE-PERSON RILEY", PersonName(last="SAMPLE-PERSON", first="RILEY")),
        ("EXAMPLETON JR CASEY", PersonName(last="EXAMPLETON", first="CASEY", suffix="JR")),
        ("DENTMOCK II TAYLOR", PersonName(last="DENTMOCK", first="TAYLOR", suffix="II")),
        ("DO MINH Q", PersonName(last="DO", first="MINH", middle="Q")),
    ],
)
def test_people_are_read_surname_first(parsed: ParsedList, name: str, person: PersonName) -> None:
    record = _record(parsed, name)

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == person


def test_a_suspension_with_a_nine_digit_zip(parsed: ParsedList) -> None:
    record = _record(parsed, "SAMPLE-PERSON RILEY")

    assert record.action is ExclusionAction.SUSPENSION
    assert record.address is not None
    assert record.address.postal_code == "62701-1234"


def test_an_exclusion_with_a_licence_and_no_address(parsed: ParsedList) -> None:
    record = _record(parsed, "EXAMPLETON JR CASEY")

    assert record.action is ExclusionAction.EXCLUSION
    assert record.license == License(number="MD99999", state="IL")
    assert record.address is None


def test_a_facility_type_makes_a_name_without_business_words_an_organization(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "MOCKRIDGE APOTHECARY")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "MOCKRIDGE APOTHECARY"
    assert record.npi == builders.NPI_B
    assert record.address == Address(
        line1="5 SAMPLE PLZ", city="ST LOUIS", state="MO", postal_code="63101"
    )


def test_business_words_decide_without_a_type(parsed: ParsedList) -> None:
    record = _record(parsed, "SAMPLE CARE SERVICES INC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "SAMPLE CARE SERVICES INC"


def test_a_professional_corporation_under_a_facility_type_stays_the_organization(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "AVERY Q MOCKPERSON PSYD PC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "AVERY Q MOCKPERSON PSYD PC"
    assert record.person_name is None
    assert record.aliases == ()


def test_a_trailing_slash_after_dba_is_ignored(parsed: ParsedList) -> None:
    record = _record(parsed, "MOCK HOLDINGS INC. D/B/A/ MOCK RIDES")

    assert record.organization_name == "MOCK HOLDINGS INC."
    assert record.aliases == ("MOCK RIDES",)


def test_rows_of_one_barred_person_are_merged_with_every_affiliation(
    parsed: ParsedList,
) -> None:
    [record] = [r for r in parsed.records if r.raw["Provider Name"] == "BARREDMOCK DANA"]

    assert record.person_name == PersonName(last="BARREDMOCK", first="DANA")
    assert record.action is ExclusionAction.DEBARMENT
    assert record.action_raw == "Barred"
    assert record.related_parties == ("MOCK TRANSPORT CO", "SAMPLE MEDICAR INC")
    assert record.aliases == ()
    assert record.raw["Affiliation"] == "MOCK TRANSPORT CO; SAMPLE MEDICAR INC"
    assert record.raw["Zip Code"] == "?; 60432"
    assert record.address == Address(line1="7 EXAMPLE CT", city="JOLIET", postal_code="?")


def test_an_affiliation_equal_to_the_name_is_no_related_party(parsed: ParsedList) -> None:
    record = _record(parsed, "SELFMOCK CLINIC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.related_parties == ()


def test_an_unreadable_date_is_left_empty(parsed: ParsedList) -> None:
    record = _record(parsed, "BADDATEMOCK ALEX")

    assert record.effective_date is None
    assert record.raw["Action Date"] == "13/45/2020"


@pytest.mark.parametrize(
    ("name", "provider_type", "kind", "expected"),
    [
        ("SAMPLEPERSON AVERY", "MEDICAR PROVIDER", EntityKind.ORGANIZATION, "SAMPLEPERSON AVERY"),
        ("SAMPLEPERSON AVERY MD", "GROUP", EntityKind.INDIVIDUAL, "SAMPLEPERSON"),
        ("SAMPLE MOCK DRUGS", "PHYSICIAN", EntityKind.ORGANIZATION, "SAMPLE MOCK DRUGS"),
        ("AVERY Q MOCKPERSON PSYD PC", "", EntityKind.INDIVIDUAL, "AVERY"),
        ("MOCKPERSON CASEY", "behavior health services", EntityKind.INDIVIDUAL, "MOCKPERSON"),
        ("MOCKPERSON CASEY", " pharmacy ", EntityKind.ORGANIZATION, "MOCKPERSON CASEY"),
    ],
)
def test_parse_party(name: str, provider_type: str, kind: EntityKind, expected: str) -> None:
    party = parse_party(name, provider_type)

    assert party.entity_kind is kind
    assert expected in (party.organization, party.person.last if party.person else None)


def test_a_row_without_a_name_raises() -> None:
    with pytest.raises(ParseError, match="no provider name"):
        parse_party("  ", "PHYSICIAN")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("60601 - 1234", "60601-1234"), ("60601-1234", "60601-1234"), (" 60601 ", "60601"),
     ("?", "?"), ("", "")],
)  # fmt: skip
def test_zip_cells(cell: str, expected: str) -> None:
    assert parse_zip(cell) == expected


def test_capabilities_are_polite_and_read_the_page() -> None:
    connector = IlHfsConnector(ScriptedFetcher())

    assert CAPABILITIES.source_id == "il_hfs"
    assert CAPABILITIES.change_signal is ChangeSignal.API_METADATA
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.source_url == SOURCE_URL
    assert CAPABILITIES.jurisdictions == frozenset({"IL"})
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.access_policy is AccessPolicy.HTTP
    assert CAPABILITIES.method is VerificationMethod.BULK_DATASET
    assert CAPABILITIES.browser_fallback is False
    assert CAPABILITIES.last_modified_is_meaningful is True
    assert CAPABILITIES.provider_type_fields == ("Provider Type",)
    assert CAPABILITIES.display_name == "Illinois HFS OIG Provider Sanctions List"
    assert connector.metadata_url == SOURCE_URL
    assert not connector.uses_browser
