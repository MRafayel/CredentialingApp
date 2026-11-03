import hashlib
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, NotModified
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.nh_dhhs import NhDhhsConnector
from credentialing.connectors.nh_dhhs.connector import COLUMNS, EFFECTIVE_DATE, SOURCE_URL
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    EntityKind,
    ExclusionAction,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import nh_builders
from tests.connectors.fixtures.builders import (
    NPI_A,
    NPI_B,
    NPI_C,
    NPI_D,
    NPI_E,
    NPI_F,
    workbook,
)
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

URL = NhDhhsConnector.download_url


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(), source_id="nh_dhhs", fetched_at=NOW, outcome=Outcome.COMPLETED, source_url=URL
    )


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return NhDhhsConnector(ScriptedFetcher()).parse(nh_builders.nh_dhhs(), _snapshot())


def _person(parsed: ParsedList, last: str) -> list[ExclusionRecord]:
    return [r for r in parsed.records if r.person_name and r.person_name.last == last]


def _organization(parsed: ParsedList, name: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.organization_name == name)


def _document(body: bytes, *, in_browser: bool = False) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=None if in_browser else datetime(2026, 10, 30, 18, 49, 46, tzinfo=UTC),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        final_url=URL,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=in_browser,
    )


_REFUSED = FetchFailure(outcome=Outcome.BLOCKED, status=403, note="HTTP 403", fetched_at=NOW)


def test_the_fixture_uses_the_connectors_columns() -> None:
    assert nh_builders.COLUMNS == COLUMNS


def test_capabilities_describe_a_polite_weekly_conditional_download() -> None:
    capabilities = NhDhhsConnector(ScriptedFetcher()).capabilities

    assert capabilities.source_id == "nh_dhhs"
    assert capabilities.jurisdictions == frozenset({"NH"})
    assert capabilities.change_signal is ChangeSignal.CONDITIONAL_GET
    assert capabilities.download_url == URL
    assert URL.startswith("https://")
    assert URL.endswith("/provider-sanctions-list-update.xlsx")
    assert capabilities.source_url == SOURCE_URL
    assert capabilities.source_url.startswith("https://")
    assert capabilities.access_policy is AccessPolicy.HTTP
    assert capabilities.method is VerificationMethod.BULK_DATASET
    assert capabilities.poll_interval.days == 7
    assert capabilities.crawl_delay.total_seconds() == 10
    assert capabilities.browser_fallback is True
    assert capabilities.display_name
    assert NhDhhsConnector.landing_url is None


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 13  # the fully empty row is not read
    assert parsed.skipped == 1  # a first name alone names nobody
    assert parsed.merged == 1
    assert len(parsed.records) == 11
    assert parsed.unreadable == {EFFECTIVE_DATE: 1}
    assert parsed.note() == (
        "13 rows, 11 records, 1 skipped; 1 merged; "
        "unreadable values: Exclusion/Sanction Effective Date 1"
    )


def test_every_record_carries_provenance(parsed: ParsedList) -> None:
    for record in parsed.records:
        assert record.source_id == "nh_dhhs"
        assert record.raw
        assert "Provider/Individual Type" in record.raw
        assert record.action_raw == record.reason
        assert record.npi not in record.related_npis


def test_a_person_with_a_middle_initial_and_a_number_npi(parsed: ParsedList) -> None:
    (record,) = _person(parsed, "Testperson")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery", middle="J.")
    assert record.npi == NPI_A
    assert record.action is ExclusionAction.SANCTION
    assert record.action_raw == "Medicare Revocation"
    assert record.effective_date == date(2019, 1, 7)
    assert record.end_date is None
    assert record.address is None
    assert record.license is None
    assert record.date_of_birth is None
    assert record.raw["Provider/Individual Type"] == "Physician"


def test_a_reason_naming_an_exclusion_is_an_exclusion(parsed: ParsedList) -> None:
    (record,) = _person(parsed, "Sampleton")

    assert record.person_name == PersonName(last="Sampleton", first="Jordan", suffix="Jr")
    assert record.npi == NPI_B
    assert record.action is ExclusionAction.EXCLUSION
    assert record.reason == "OIG LEIE and SAM Exclusion"


def test_a_suffix_after_a_comma_a_two_word_first_name_and_a_text_date(
    parsed: ParsedList,
) -> None:
    (record,) = _person(parsed, "Mockfield")

    assert record.person_name == PersonName(
        last="Mockfield", first="Riley", middle="Ann", suffix="IV"
    )
    assert record.npi is None
    assert record.raw["National Provider Identifier (NPI)"] == "N/A"
    assert record.effective_date == date(2024, 3, 15)
    assert record.raw["Provider/Individual Type"] == "RECIPT/BENEFICIARY"


def test_a_business_on_a_persons_row_is_a_related_party(parsed: ParsedList) -> None:
    (record,) = _person(parsed, "Exampleton")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.organization_name is None
    assert record.related_parties == ("Exampleton Home Care LLC",)
    assert record.aliases == ()


def test_organizations_and_a_fein_that_is_not_an_npi(parsed: ParsedList) -> None:
    supplier = _organization(parsed, "Sample Medical Supply, INC.")
    care = _organization(parsed, "Sampletown Care @ Home, LLC")

    assert supplier.entity_kind is EntityKind.ORGANIZATION
    assert supplier.person_name is None
    assert supplier.npi is None
    assert supplier.raw["National Provider Identifier (NPI)"] == nh_builders.FAKE_FEIN
    assert supplier.action is ExclusionAction.EXCLUSION
    assert supplier.effective_date == date(2016, 9, 30)
    assert care.entity_kind is EntityKind.ORGANIZATION
    assert care.npi is None
    assert care.action is ExclusionAction.SANCTION


def test_two_dates_in_one_cell_are_unreadable_not_guessed(parsed: ParsedList) -> None:
    (record,) = _person(parsed, "Fakerly")

    assert record.effective_date is None
    assert record.reason == "Program Related Conviction 9/19/23 OIG"


def test_several_npis_in_a_cell(parsed: ParsedList) -> None:
    (record,) = _person(parsed, "Doubleton")

    assert record.npi == NPI_C
    assert record.related_npis == (nh_builders.NPI_G,)
    assert record.action is ExclusionAction.SANCTION


def test_one_person_on_several_rows(parsed: ParsedList) -> None:
    records = _person(parsed, "Repeatson")

    assert [(r.npi, r.effective_date) for r in records] == [
        (NPI_D, date(2014, 11, 14)),
        (NPI_E, date(2014, 11, 14)),
        (NPI_F, date(2013, 11, 6)),
    ]
    merged = records[2]
    assert merged.raw["Provider/Individual Type"] == "Physician; Physician Assistant"
    assert merged.raw["Exclusion/Sanction Reason"] == (
        "Medicare Revocation; Medicare revocation (CMS)"
    )
    assert merged.reason == "Medicare Revocation"


def test_a_file_without_the_header_fails_to_parse() -> None:
    body = workbook([("Last Name", "First Name"), ("Testperson", "Avery")])

    with pytest.raises(ParseError, match="header"):
        NhDhhsConnector(ScriptedFetcher()).parse(body, _snapshot())


@pytest.mark.anyio
async def test_a_poll_is_one_conditional_request() -> None:
    fetcher = ScriptedFetcher(_document(nh_builders.nh_dhhs()))

    result = await NhDhhsConnector(fetcher).fetch(None)

    assert fetcher.requests == [(URL, ChangeSignal.CONDITIONAL_GET)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == URL
    assert result.snapshot.last_modified == datetime(2026, 10, 30, 18, 49, 46, tzinfo=UTC)
    assert result.snapshot.row_count == 11
    assert not (result.snapshot.note or "").startswith("browser fallback")
    assert result.records is not None
    assert len(result.records) == 11


@pytest.mark.anyio
async def test_an_unchanged_file_is_not_parsed() -> None:
    previous = _snapshot().model_copy(
        update={"content_hash": "abc", "last_modified": NOW, "row_count": 11}
    )
    unchanged = NotModified(
        reason="HTTP 304", etag=None, last_modified=NOW, fetched_at=NOW, status=304
    )

    result = await NhDhhsConnector(ScriptedFetcher(unchanged)).fetch(previous)

    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (HTTP 304)"
    assert result.records is None


@pytest.mark.anyio
async def test_a_refused_request_stays_blocked_without_a_browser() -> None:
    fetcher = ScriptedFetcher(_REFUSED)

    result = await NhDhhsConnector(fetcher).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.records is None
    assert fetcher.requests == [(URL, ChangeSignal.CONDITIONAL_GET)]


@pytest.mark.anyio
async def test_a_refused_request_is_repeated_in_the_browser() -> None:
    browser = ScriptedFetcher(_document(nh_builders.nh_dhhs(), in_browser=True))

    result = await NhDhhsConnector(ScriptedFetcher(_REFUSED), browser=browser).fetch(None)

    assert browser.requests == [(URL, ChangeSignal.CONDITIONAL_GET)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == (
        "browser fallback; 13 rows, 11 records, 1 skipped; 1 merged; "
        "unreadable values: Exclusion/Sanction Effective Date 1"
    )
    assert result.records is not None
    assert len(result.records) == 11


@pytest.mark.anyio
async def test_a_challenge_the_browser_cannot_clear_stays_blocked() -> None:
    challenge = FetchFailure(
        outcome=Outcome.BLOCKED,
        status=403,
        note="challenge page not cleared (HTTP 403)",
        fetched_at=NOW,
    )

    result = await NhDhhsConnector(
        ScriptedFetcher(_REFUSED), browser=ScriptedFetcher(challenge)
    ).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == (
        "HTTP 403; browser fallback: challenge page not cleared (HTTP 403)"
    )
    assert result.records is None


@pytest.mark.anyio
async def test_a_file_that_is_not_a_workbook_fails() -> None:
    page = _document(b"<html><body>Page not found</body></html>")

    result = await NhDhhsConnector(ScriptedFetcher(page)).fetch(None)

    assert result.snapshot.outcome is Outcome.FAILED
    assert (result.snapshot.note or "").startswith("parse failed: ")
    assert result.records is None
