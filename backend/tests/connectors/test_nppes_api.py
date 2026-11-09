"""The NPPES API connector: requests, mapping, failures and what it keeps, offline."""

import hashlib
import json
import logging
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from credentialing.connectors.framework.http import (
    FetchedDocument,
    FetchFailure,
    FetchOutcome,
    HttpFetcher,
    NotModified,
)
from credentialing.connectors.nppes.api import (
    API_URL,
    CAPABILITIES,
    NppesApiConnector,
    line_hash,
    search_url,
)
from credentialing.domain.enums import (
    ChangeSignal,
    CredentialType,
    EntityKind,
    NpiStatus,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.models import Address, NpiTaxonomy, PersonName
from credentialing.domain.ports import NpiQuery, NpiRegistryConnector
from credentialing.infrastructure.logging import configure_logging
from tests.connectors.fixtures import nppes_api_builders as b
from tests.fakes import FixedClock, ScriptedFetcher
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio


def _document(body: bytes, url: str = API_URL) -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=NOW,
        content_type="application/json",
        final_url=url,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


def _connector(*outcomes: FetchOutcome) -> tuple[NppesApiConnector, ScriptedFetcher]:
    fetcher = ScriptedFetcher(*outcomes)
    return NppesApiConnector(fetcher), fetcher


def _parameters(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def test_capabilities_say_on_demand_official_api_never_polled() -> None:
    assert CAPABILITIES.source_id == "nppes_api"
    assert CAPABILITIES.credential_type is CredentialType.NPI
    assert CAPABILITIES.method is VerificationMethod.OFFICIAL_API
    assert CAPABILITIES.poll_interval == timedelta(0)
    assert CAPABILITIES.crawl_delay == timedelta(seconds=1)
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.source_url == "https://npiregistry.cms.hhs.gov/"
    assert isinstance(NppesApiConnector(ScriptedFetcher()), NpiRegistryConnector)


async def test_a_lookup_is_one_request_for_the_number_with_the_crawl_delay() -> None:
    connector, fetcher = _connector(_document(b.result(b.individual_result())))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    ((url, signal),) = fetcher.requests
    assert url == f"https://npiregistry.cms.hhs.gov/api/?version=2.1&number={b.PERSON_NPI}"
    assert signal is ChangeSignal.CONTENT_HASH
    assert fetcher.fetch_crawl_delays == [timedelta(seconds=1)]
    snapshot = answer.snapshot
    assert snapshot.outcome is Outcome.COMPLETED
    assert snapshot.source_id == "nppes_api"
    assert snapshot.source_url == url
    assert (snapshot.note, snapshot.row_count, snapshot.last_modified) == ("lookup: found", 1, None)
    assert answer.found is True
    assert answer.result_count == 1


def test_a_search_sends_only_the_filled_fields_escaped_with_the_limit_clamped() -> None:
    query = NpiQuery(
        last_name="O'Testperson & Co",
        organization_name=None,
        entity_kind=EntityKind.INDIVIDUAL,
        state="NY",
        city="Sample Ville",
    )

    low = _parameters(search_url(query, limit=5))
    high = _parameters(search_url(query, limit=500))
    organizations = _parameters(
        search_url(
            NpiQuery(organization_name="Care*", entity_kind=EntityKind.ORGANIZATION), limit=50
        )
    )

    assert low == {
        "version": ["2.1"],
        "last_name": ["O'Testperson & Co"],
        "enumeration_type": ["NPI-1"],
        "state": ["NY"],
        "city": ["Sample Ville"],
        "limit": ["10"],
    }
    assert "%26" in search_url(query, limit=5)
    assert high["limit"] == ["200"]
    assert organizations == {
        "version": ["2.1"],
        "organization_name": ["Care*"],
        "enumeration_type": ["NPI-2"],
        "limit": ["50"],
    }


async def test_a_search_snapshot_keeps_no_query_string() -> None:
    connector, fetcher = _connector(
        _document(b.result(b.individual_result(), b.organization_result()))
    )

    answer = await connector.search_npis(NpiQuery(last_name="Testperson"), limit=50)

    assert "last_name=Testperson" in fetcher.requests[0][0]
    assert answer.snapshot.source_url == "https://npiregistry.cms.hhs.gov/api/"
    assert answer.snapshot.note == "search: 2 results"
    assert answer.records is not None
    assert [r.npi for r in answer.records] == [b.PERSON_NPI, b.ORGANIZATION_NPI]


async def test_an_individual_maps_every_field() -> None:
    connector, _ = _connector(_document(b.result(b.individual_result())))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert answer.records is not None
    (record,) = answer.records
    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.status is NpiStatus.ACTIVE
    assert record.person_name == PersonName(last="TESTPERSON", first="AVERY", middle="QUINN")
    assert (record.name_prefix, record.credential) == ("Dr.", "MD")
    assert record.organization_name is None
    assert record.other_names == ("EXAMPLEFORMER, AVERY QUINN",)
    assert (record.sole_proprietor, record.organization_subpart) == (False, None)
    assert record.enumeration_date == date(2005, 11, 5)
    assert record.last_updated == date(2026, 1, 1)
    assert record.certification_date == date(2026, 1, 1)
    assert record.practice_address == Address(
        line1="100 SAMPLE WAY",
        line2="SUITE 2",
        city="SAMPLEVILLE",
        state="NY",
        postal_code="999010000",
        country="US",
    )
    assert record.mailing_address is not None
    assert (record.mailing_address.line1, record.mailing_address.postal_code) == (
        "PO BOX 9",
        "99901",
    )
    assert record.taxonomies == (
        NpiTaxonomy(code="207Q00000X", primary=True, license_number="SAMPLE-1", license_state="NY"),
        NpiTaxonomy(code="207R00000X", primary=False, license_state="NJ"),
    )
    assert record.authorized_official is None
    assert (record.replacement_npi, record.deactivation_date, record.reactivation_date) == (
        None,
        None,
        None,
    )
    assert record.snapshot_id == answer.snapshot.id
    assert record.in_file is True
    assert (record.first_seen_at, record.updated_at) == (None, None)


async def test_an_organization_maps_its_names_official_and_flags() -> None:
    connector, _ = _connector(_document(b.result(b.organization_result())))

    answer = await connector.lookup_npi(b.ORGANIZATION_NPI)

    assert answer.records is not None
    (record,) = answer.records
    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.person_name is None
    assert record.name_prefix is None
    assert record.organization_name == "SAMPLEVILLE CARE GROUP LLC"
    assert record.other_names == ("SAMPLEVILLE CLINIC", "SAMPLEVILLE HEALTH SYSTEM")
    assert record.organization_subpart is True
    assert record.sole_proprietor is None
    official = record.authorized_official
    assert official is not None
    assert official.name == PersonName(last="EXAMPLE", first="JORDAN")
    assert (official.title, official.prefix, official.credential) == ("ADMINISTRATOR", None, None)
    assert record.taxonomies[0] == NpiTaxonomy(code="261QM1300X", primary=True)
    assert record.taxonomies[1].group == "193400000X - Single Specialty Group"
    assert record.practice_address is not None
    assert record.practice_address.postal_code == "99902"


async def test_the_line_hash_is_marked_and_covers_the_canonical_result() -> None:
    item = b.individual_result()
    connector, _ = _connector(_document(b.result(item)))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert answer.records is not None
    canonical = json.dumps(item, sort_keys=True, separators=(",", ":"))
    expected = "api:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert answer.records[0].line_hash == expected == line_hash(item)


async def test_no_result_is_completed_and_not_found() -> None:
    connector, _ = _connector(_document(b.no_result()))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert answer.snapshot.outcome is Outcome.COMPLETED
    assert answer.records == ()
    assert (answer.found, answer.result_count) == (False, 0)
    assert answer.snapshot.note == "lookup: no result (not active in NPPES)"


async def test_an_error_answer_is_failed_and_keeps_only_description_and_field() -> None:
    connector, _ = _connector(_document(b.errors(("NPI must be 10 digits", "number", "06"))))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert answer.snapshot.outcome is Outcome.FAILED
    assert answer.snapshot.note == "API error: NPI must be 10 digits (number)"
    assert answer.records is None
    assert answer.found is None
    assert answer.snapshot.content_hash is None


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"<!doctype html><html></html>", "unreadable answer: not JSON"),
        (b"[]", "unreadable answer: not a JSON object"),
        (b'{"result_count": 1}', "unreadable answer: no results"),
        (b'{"result_count": "1", "results": []}', "unreadable answer: result_count is not a count"),
    ],
)
async def test_an_answer_that_is_not_the_api_json_is_failed(body: bytes, reason: str) -> None:
    connector, _ = _connector(_document(body))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert (answer.snapshot.outcome, answer.snapshot.note) == (Outcome.FAILED, reason)
    assert answer.records is None


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (b.individual_result(status="D"), "result 1: status is not A"),
        ({**b.individual_result(), "enumeration_type": "NPI-3"}, "result 1: enumeration_type"),
        ({**b.individual_result(), "number": "12345"}, "result 1 does not validate (number)"),
        (b.individual_result(last_updated="01/02/2026"), "(basic.last_updated)"),
        (b.individual_result(first_name="", last_name=""), "result 1 does not validate"),
    ],
)
async def test_a_result_that_does_not_map_fails_the_answer_without_values(
    item: b.Json, reason: str
) -> None:
    connector, _ = _connector(_document(b.result(item)))

    answer = await connector.search_npis(NpiQuery(last_name="Testperson"), limit=10)

    assert answer.snapshot.outcome is Outcome.FAILED
    assert answer.snapshot.note is not None
    assert reason in answer.snapshot.note
    assert "TESTPERSON" not in answer.snapshot.note
    assert "01/02/2026" not in answer.snapshot.note


async def test_a_lookup_answered_with_another_npi_is_failed() -> None:
    connector, _ = _connector(_document(b.result(b.organization_result())))

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert answer.snapshot.note == "unreadable answer: the answer is for another NPI"


@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.SOURCE_UNAVAILABLE, Outcome.FAILED])
async def test_a_refused_request_keeps_its_outcome_and_never_reads_not_found(
    outcome: Outcome,
) -> None:
    failure = FetchFailure(outcome=outcome, status=429, note="HTTP 429", fetched_at=NOW)
    connector, _ = _connector(failure)

    answer = await connector.search_npis(NpiQuery(last_name="Testperson"), limit=10)

    assert (answer.snapshot.outcome, answer.snapshot.note) == (outcome, "HTTP 429")
    assert answer.snapshot.source_url == API_URL
    assert (answer.records, answer.found) == (None, None)


async def test_an_unchanged_answer_to_a_plain_request_is_failed() -> None:
    unchanged = NotModified(
        reason="HTTP 304", etag=None, last_modified=None, fetched_at=NOW, status=304
    )
    connector, _ = _connector(unchanged)

    answer = await connector.lookup_npi(b.PERSON_NPI)

    assert answer.snapshot.outcome is Outcome.FAILED


async def test_no_name_npi_or_query_string_reaches_a_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[httpx2.URL] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.url)
        return httpx2.Response(
            200,
            content=b.result(b.individual_result()),
            headers={"content-type": "application/json"},
        )

    async def no_wait(_seconds: float) -> None:
        return None

    caplog.set_level(logging.DEBUG)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        connector = NppesApiConnector(HttpFetcher(client, FixedClock(NOW), sleep=no_wait))
        await connector.lookup_npi(b.PERSON_NPI)
        await connector.search_npis(NpiQuery(last_name="Testperson", city="Sampleville"), limit=10)

    assert parse_qs(sent[1].query.decode())["last_name"] == ["Testperson"]
    ours = [record for record in caplog.records if record.name.startswith("credentialing.")]
    assert ours
    text = "\n".join(record.getMessage() for record in ours)
    for secret in (b.PERSON_NPI, "Testperson", "TESTPERSON", "Sampleville", "AVERY", "?"):
        assert secret not in text
    assert "kind=lookup outcome=completed results=1" in text
    assert "kind=search outcome=completed results=1" in text


def test_the_http_library_request_lines_stay_quiet_whatever_the_level() -> None:
    """httpx2 logs every request's full URL at INFO; the logging setup keeps it at WARNING."""
    library = logging.getLogger("httpx2")
    root = logging.getLogger()
    saved = (library.level, root.level, list(root.handlers))
    try:
        configure_logging("DEBUG")

        assert not library.isEnabledFor(logging.INFO)
    finally:
        library.setLevel(saved[0])
        root.setLevel(saved[1])
        root.handlers[:] = saved[2]
