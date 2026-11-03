import hashlib
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.co_hcpf import CoHcpfConnector
from credentialing.connectors.co_hcpf.connector import (
    CAPABILITIES,
    COLUMNS,
    LANDING_URL,
    doing_business_as,
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
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    PersonName,
    SourceSnapshot,
)
from tests.connectors.fixtures import builders, co_builders, fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

FILE_URL = (
    "https://hcpf.colorado.gov/sites/hcpf/files/Terminations%20for%20cause%20ef.%2010.9.26.xlsx"
)
REDIRECTED_URL = (
    "https://hcpf.colorado.gov/sites/g/files/lrnvjt4251/files/"
    "Terminations%20for%20cause%20ef.%2010.9.26.xlsx"
)

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="co_hcpf",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=FILE_URL,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return CoHcpfConnector(ScriptedFetcher()).parse(co_builders.co_hcpf(), SNAPSHOT)


def _record(parsed: ParsedList, name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Provider Name"].strip() == name]
    return record


def _document(
    body: bytes, url: str, *, in_browser: bool = False, content_type: str | None = None
) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=datetime(2026, 10, 17, 20, 59, 21, tzinfo=UTC),
        content_type=content_type,
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
        fetched_in_browser=in_browser,
    )


def _landing(*, in_browser: bool = False) -> FetchedDocument:
    return _document(
        fixture_bytes("co_hcpf_landing.html"),
        LANDING_URL,
        in_browser=in_browser,
        content_type="text/html; charset=UTF-8",
    )


_REFUSED = FetchFailure(outcome=Outcome.BLOCKED, status=403, note="HTTP 403", fetched_at=NOW)


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 15
    assert parsed.skipped == 1
    assert parsed.merged == 2
    assert len(parsed.records) == 12
    assert parsed.unreadable == {"Termination Effective Date": 1}
    assert parsed.note() == (
        "15 rows, 12 records, 1 skipped; 2 merged; unreadable values: Termination Effective Date 1"
    )
    assert all(r.action is ExclusionAction.TERMINATION for r in parsed.records)
    assert all(set(r.raw) == set(COLUMNS) for r in parsed.records)


def test_a_comma_name_is_last_name_first(parsed: ParsedList) -> None:
    record = _record(parsed, "Sampleton, Avery")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Sampleton", first="Avery")
    assert record.npi == builders.NPI_A
    assert record.aliases == ()
    assert record.reason == "10 CCR 2505-10, Section 8.076.1.7.e"
    assert record.effective_date == date(2021, 3, 4)


def test_a_person_without_npi_keeps_the_middle_initial(parsed: ParsedList) -> None:
    record = _record(parsed, "MOCKWELL, JORDAN K")

    assert record.person_name == PersonName(last="MOCKWELL", first="JORDAN", middle="K")
    assert record.npi is None
    assert record.related_npis == ()
    assert record.raw["NPI"] == "N/A"


def test_a_comma_less_name_without_business_words_is_first_name_first(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "Riley Testperson")
    credentialed = _record(parsed, "Jamie Q Sampleperson LPC")

    assert record.person_name == PersonName(last="Testperson", first="Riley")
    assert credentialed.person_name == PersonName(
        last="Sampleperson", first="Jamie", middle="Q", suffix="LPC"
    )
    assert credentialed.npi == builders.NPI_A
    assert credentialed.effective_date == date(2019, 6, 15)


def test_an_organization_named_only_by_its_specialty_is_an_organization(
    parsed: ParsedList,
) -> None:
    # Read as the person "Dentistry, Sampleford" until the specialty words joined the list.
    record = _record(parsed, "Sampleford Family Dentistry")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert (record.organization_name, record.person_name) == ("Sampleford Family Dentistry", None)


def test_an_organization_with_two_npis_and_a_dba(parsed: ParsedList) -> None:
    record = _record(parsed, "Sample Home Health Care, LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sample Home Health Care, LLC"
    assert record.person_name is None
    assert record.aliases == ("Sample Care",)
    assert record.related_parties == ()
    assert record.npi == builders.NPI_C
    assert record.related_npis == (builders.NPI_D,)


def test_a_person_s_business_dba_is_an_alias_not_a_related_party(parsed: ParsedList) -> None:
    record = _record(parsed, "Exampleton, Quinn")

    assert record.person_name == PersonName(last="Exampleton", first="Quinn")
    assert record.aliases == ("Exampleton Wellness Clinic",)
    assert record.related_parties == ()


def test_invalid_ten_digits_are_dropped_and_the_rest_are_related(parsed: ParsedList) -> None:
    record = _record(parsed, "Mockville Pharmacy Inc")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.npi == co_builders.NPI_H
    assert record.related_npis == (co_builders.NPI_I,)
    assert builders.INVALID_TEN_DIGITS in record.raw["NPI"]
    assert record.reason == "42 CFR 455.416(c)"


def test_a_parenthesized_list_gives_aliases(parsed: ParsedList) -> None:
    record = _record(parsed, "MOCKCARE TRANSPORT LLC (MOCKRIDE, TESTRIDE)")

    assert record.organization_name == "MOCKCARE TRANSPORT LLC"
    assert record.aliases == ("MOCKRIDE", "TESTRIDE")


def test_rows_repeating_an_identity_are_merged_without_losing_the_dba(
    parsed: ParsedList,
) -> None:
    demo = _record(parsed, "Demo, Casey")
    repeated = _record(parsed, "MOCKFORD, PAT")

    assert demo.aliases == ("Demo Therapy Services",)
    assert demo.raw['"Doing Business As" Name'] == "Demo Therapy Services"
    assert demo.npi == builders.NPI_F
    assert repeated.npi == co_builders.NPI_G
    assert repeated.raw["Termination Authority"] == "10 C.C.R. 2505-10, Section 8.076.1.7.f"


def test_an_unreadable_date_is_counted_and_left_empty(parsed: ParsedList) -> None:
    record = _record(parsed, "TESTWOOD, MORGAN")

    assert record.effective_date is None
    assert record.raw["Termination Effective Date"] == "pending review"


def test_a_row_without_a_name_is_skipped(parsed: ParsedList) -> None:
    assert parsed.skipped == 1
    assert all(r.raw["Provider Name"] for r in parsed.records)
    assert date(2017, 2, 2) not in {r.effective_date for r in parsed.records}


@pytest.mark.parametrize(
    ("text", "names"),
    [
        (" ", ()),
        (None, ()),
        ("Sample Care", ("Sample Care",)),
        ("DBA Sample Care", ("Sample Care",)),
        ("Sample Care aka Mock Care", ("Sample Care", "Mock Care")),
    ],
)
def test_doing_business_as_names(text: str | None, names: tuple[str, ...]) -> None:
    assert doing_business_as(text) == names


def test_another_header_fails_the_file() -> None:
    body = builders.workbook([("Provider Name", "NPI", "Termination Effective Date")])

    with pytest.raises(ParseError):
        CoHcpfConnector(ScriptedFetcher()).parse(body, SNAPSHOT)


def test_capabilities() -> None:
    assert ConnectorCapabilities.model_validate(CAPABILITIES.model_dump()) == CAPABILITIES
    assert CAPABILITIES.source_id == "co_hcpf"
    assert CAPABILITIES.jurisdictions == frozenset({"CO"})
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.method is VerificationMethod.BULK_DATASET
    assert CAPABILITIES.access_policy is AccessPolicy.HTTP
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.browser_fallback is True
    assert CAPABILITIES.source_url == LANDING_URL
    assert CAPABILITIES.source_url.startswith("https://")
    assert CAPABILITIES.display_name == "Colorado HCPF Terminated Provider List"
    assert CoHcpfConnector.landing_url == LANDING_URL
    assert CoHcpfConnector(ScriptedFetcher(), browser=ScriptedFetcher()).uses_browser


def test_the_terminations_link_is_found_and_the_reinstated_list_is_not() -> None:
    page = fixture_bytes("co_hcpf_landing.html").decode()
    reinstated_first = (
        '<a href="/sites/hcpf/files/Reinstated%20Providers%20ef.%2011.6.26.xlsx">R</a>'
        '<a href="/sites/hcpf/files/Terminated%20Providers%20ef.%2011.6.26.xlsx">T</a>'
    )
    renamed = (
        '<a href="/sites/g/files/lrnvjt4251/files/TERMINATIONS%20FOR%20CAUSE%2012.1.26.XLSX">T</a>'
    )
    connector = CoHcpfConnector(ScriptedFetcher())

    assert connector.find_link(page) == FILE_URL
    assert connector.find_link(reinstated_first) == (
        "/sites/hcpf/files/Terminated%20Providers%20ef.%2011.6.26.xlsx"
    )
    assert connector.find_link(renamed).endswith("12.1.26.XLSX")
    with pytest.raises(ParseError):
        connector.find_link('<a href="/sites/hcpf/files/Reinstated%20Providers.xlsx">R</a>')


@pytest.mark.anyio
async def test_a_new_link_is_downloaded_over_http() -> None:
    body = co_builders.co_hcpf()
    fetcher = ScriptedFetcher(_landing(), _document(body, REDIRECTED_URL))

    result = await CoHcpfConnector(fetcher).fetch(None)

    assert fetcher.requests == [
        (LANDING_URL, ChangeSignal.CONTENT_HASH),
        (FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == FILE_URL
    assert result.snapshot.note == (
        "15 rows, 12 records, 1 skipped; 2 merged; unreadable values: Termination Effective Date 1"
    )
    assert result.records is not None
    assert len(result.records) == 12
    assert all(r.snapshot_id == result.snapshot.id for r in result.records)


@pytest.mark.anyio
async def test_an_unchanged_link_is_one_request() -> None:
    previous = SNAPSHOT.model_copy(update={"content_hash": "ab" * 32, "row_count": 12})
    fetcher = ScriptedFetcher(_landing())

    result = await CoHcpfConnector(fetcher).fetch(previous)

    assert fetcher.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.note == "unchanged (link unchanged)"
    assert result.records is None


@pytest.mark.anyio
async def test_a_refused_landing_page_is_read_in_the_browser_and_the_file_over_http() -> None:
    body = co_builders.co_hcpf()
    fetcher = ScriptedFetcher(_REFUSED, _document(body, REDIRECTED_URL))
    browser = ScriptedFetcher(_landing(in_browser=True))

    result = await CoHcpfConnector(fetcher, browser=browser).fetch(None)

    assert fetcher.requests == [
        (LANDING_URL, ChangeSignal.CONTENT_HASH),
        (FILE_URL, ChangeSignal.CONTENT_HASH),
    ]
    assert browser.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert (result.snapshot.note or "").startswith("browser fallback; 15 rows, 12 records")
    assert result.records


@pytest.mark.anyio
async def test_a_refused_download_is_repeated_in_the_browser() -> None:
    body = co_builders.co_hcpf()
    fetcher = ScriptedFetcher(_landing(), _REFUSED)
    browser = ScriptedFetcher(_document(body, REDIRECTED_URL, in_browser=True))

    result = await CoHcpfConnector(fetcher, browser=browser).fetch(None)

    assert browser.requests == [(FILE_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.COMPLETED
    assert result.snapshot.source_url == FILE_URL
    assert (result.snapshot.note or "").startswith("browser fallback; ")
    assert result.records


@pytest.mark.anyio
async def test_a_refusal_without_a_browser_is_blocked_never_empty() -> None:
    fetcher = ScriptedFetcher(_REFUSED)

    result = await CoHcpfConnector(fetcher).fetch(None)

    assert fetcher.requests == [(LANDING_URL, ChangeSignal.CONTENT_HASH)]
    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.records is None


@pytest.mark.anyio
async def test_a_refusal_in_the_browser_too_stays_blocked() -> None:
    challenge = FetchFailure(
        outcome=Outcome.BLOCKED,
        status=403,
        note="challenge page not cleared (HTTP 403)",
        fetched_at=NOW,
    )

    result = await CoHcpfConnector(
        ScriptedFetcher(_REFUSED), browser=ScriptedFetcher(challenge)
    ).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.snapshot.note == (
        "HTTP 403; browser fallback: challenge page not cleared (HTTP 403)"
    )
    assert result.records is None
