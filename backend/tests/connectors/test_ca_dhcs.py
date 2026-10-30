import hashlib
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from credentialing.connectors.ca_dhcs import CaDhcsConnector
from credentialing.connectors.ca_dhcs.connector import first_address, split_suffix
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)
from tests.connectors.fixtures import builders, fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW, make_npi

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="ca_dhcs",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url="https://data.chhs.ca.gov/dataset/x/resource/y/download/list.csv",
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return CaDhcsConnector(ScriptedFetcher()).parse(fixture_bytes("ca_dhcs.csv"), SNAPSHOT)


def _record(parsed: ParsedList, last: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Last Name"] == last]
    return record


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 7
    assert len(parsed.records) == 5
    assert parsed.merged == 2
    assert parsed.skipped == 0
    assert parsed.unreadable == {}
    assert all(r.action is ExclusionAction.SUSPENSION for r in parsed.records)
    assert all(r.end_date is None for r in parsed.records)
    assert all("" not in r.raw for r in parsed.records)


def test_a_person_with_suffix_aliases_npi_and_medi_cal_number(parsed: ParsedList) -> None:
    record = _record(parsed, "Testperson, Jr.")

    assert record.person_name == PersonName(
        last="Testperson", first="Avery", middle="J.", suffix="Jr."
    )
    assert record.aliases == ("Testperson, Avery James", "Maidenmock, Avery")
    assert record.related_parties == ()
    assert record.npi == make_npi("100000007")
    assert record.state_provider_id == "00A123456"
    assert record.license == License(number="A 12345", state="CA")
    assert record.address == Address(
        line1="1420 Sample Ave., Ste 209", city="Sampleville", state="CA", postal_code="94122"
    )
    assert record.effective_date == date(2019, 3, 7)


def test_an_organization_row_names_its_owner_as_a_related_party(parsed: ParsedList) -> None:
    record = _record(parsed, "Sample Medical Supply")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sample Medical Supply"
    assert record.person_name is None
    assert record.related_parties == ("Ownerperson, Riley",)
    assert record.npi is None
    assert record.license is None
    assert record.address == Address(city="Testtown", state="CA")


def test_a_persons_dba_is_an_alias_and_an_unmarked_business_a_related_party(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "Exampledoc")

    assert record.person_name == PersonName(last="Exampledoc", first="Jordan")
    assert record.aliases == ("Mock Wellness Clinic",)
    assert record.related_parties == ("Sample Family Pharmacy, Inc.",)
    assert record.npi == make_npi("100000008")
    assert record.related_npis == (make_npi("100000009"),)
    assert record.state_provider_id is None
    assert record.address == Address(
        line1="832 South Mock Ave., #E", city="Fakecity", state="CA", postal_code="90640"
    )


def test_a_professional_corporation_row_stays_an_organization(parsed: ParsedList) -> None:
    record = _record(parsed, "Casey Sampledent DDS, Inc.")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Casey Sampledent DDS, Inc."
    assert record.aliases == ()
    assert record.npi == make_npi("100000010")


def test_placeholders_in_every_spelling(parsed: ParsedList) -> None:
    record = _record(parsed, "Fakename")

    assert record.person_name == PersonName(last="Fakename", first="Quinn")
    assert record.npi is None
    assert record.license is None
    assert record.state_provider_id == "00B654321"


def test_rows_repeating_an_identity_become_one_record(parsed: ParsedList) -> None:
    record = _record(parsed, "Fakename")

    assert record.raw["Provider Type"] == "In-Home Supportive Services; Registered Nurse"
    assert record.raw["Address(es)"] == "Testtown, CA"
    assert record.raw["Date of Suspension"] == "1/2/2024"
    assert record.aliases == ("Fakename, Q.",)
    assert record.address == Address(city="Testtown", state="CA")
    assert record.effective_date == date(2024, 1, 2)
    assert parsed.note() == "7 rows, 5 records, 0 skipped; 2 merged"


def test_an_exact_repeat_parses_as_the_single_row(parsed: ParsedList) -> None:
    record = _record(parsed, "Sample Medical Supply")

    assert record.raw["Provider Type"] == "Durable Medical Equipment; Owner"
    assert record.raw["Address(es)"] == "Testtown, CA"
    assert record.related_parties == ("Ownerperson, Riley",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Abaya, Jr.", ("Abaya", "Jr.")),
        ("Sample, III", ("Sample", "III")),
        ("Smith-Jones", ("Smith-Jones", None)),
        ("Sample, Mock", ("Sample, Mock", None)),
    ],
)
def test_split_suffix(text: str, expected: tuple[str, str | None]) -> None:
    assert split_suffix(text) == expected


def test_first_address_keeps_a_street_without_zip() -> None:
    assert first_address("15413 Mockmead Ct., Sample Heights, CA") == Address(
        line1="15413 Mockmead Ct.", city="Sample Heights", state="CA"
    )
    assert first_address("n/a") is None


def test_read_metadata() -> None:
    body = fixture_bytes("ca_dhcs.csv")

    metadata = CaDhcsConnector(ScriptedFetcher()).read_metadata(
        builders.ca_dhcs_resource_show(body)
    )

    assert metadata.url.endswith("/download/suspended-ineligible-list-september-2026.csv")
    assert metadata.hash == hashlib.md5(body, usedforsecurity=False).hexdigest()
    assert metadata.last_modified == datetime(2026, 10, 12, 17, 41, 37, 794529, tzinfo=UTC)


@pytest.mark.parametrize(
    "answer",
    [
        b"<html>maintenance</html>",
        b'{"success": false, "result": {"url": "x", "hash": "' + b"0" * 32 + b'"}}',
        b'{"success": true, "result": {"url": "x", "hash": "not-a-hash"}}',
    ],
)
def test_an_unexpected_metadata_answer_raises(answer: bytes) -> None:
    with pytest.raises(ParseError):
        CaDhcsConnector(ScriptedFetcher()).read_metadata(answer)


def test_the_connector_waits_the_crawl_delay() -> None:
    assert CaDhcsConnector(ScriptedFetcher()).capabilities.crawl_delay.total_seconds() == 10
