from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.oig_leie import OigLeieConnector
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="oig_leie",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=OigLeieConnector.download_url,
)


def _parse(body: bytes) -> ParsedList:
    connector = OigLeieConnector(ScriptedFetcher())
    return connector.parse(body, SNAPSHOT)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return _parse(fixture_bytes("oig_leie.csv"))


def _by_last(parsed: ParsedList, last: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.person_name and r.person_name.last == last)


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 8
    assert len(parsed.records) == 7
    assert parsed.skipped == 1
    assert parsed.unreadable == {"EXCLDATE": 1}


def test_an_individual_row(parsed: ParsedList) -> None:
    record = _by_last(parsed, "TESTPERSON")

    assert record.source_id == "oig_leie"
    assert record.snapshot_id == SNAPSHOT.id
    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="TESTPERSON", first="AVERY", middle="J")
    assert record.npi == "1000000012"
    assert record.date_of_birth == date(1970, 1, 1)
    assert record.address == Address(
        line1="100 SAMPLE ST", city="SAMPLEVILLE", state="NY", postal_code="10001"
    )
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "1128b4"
    assert record.effective_date == date(2014, 3, 1)
    assert record.end_date is None
    assert record.reason is None
    assert record.raw["SPECIALTY"] == "NURSE/NURSES AIDE"
    assert record.valid_from == NOW


def test_a_business_row(parsed: ParsedList) -> None:
    record = next(r for r in parsed.records if r.entity_kind is EntityKind.ORGANIZATION)

    assert record.person_name is None
    assert record.organization_name == "SAMPLEVILLE CARE GROUP LLC"
    assert record.npi is None
    assert record.address is not None
    assert record.address.postal_code == "02134"


def test_reinstatement_date_and_quoted_cells(parsed: ParsedList) -> None:
    record = _by_last(parsed, "EXAMPLEDOC")

    assert record.end_date == date(2015, 5, 10)
    assert record.raw["GENERAL"] == "MEDICAL PRACTICE, MD"
    assert record.raw["UPIN"] == "A12345"


def test_an_unreadable_date_keeps_the_row(parsed: ParsedList) -> None:
    record = _by_last(parsed, "FAKENAME")

    assert record.effective_date is None
    assert record.raw["EXCLDATE"] == "2019X345"


def test_a_nine_digit_npi_is_dropped_but_kept_raw(parsed: ParsedList) -> None:
    record = _by_last(parsed, "MOCKSON")

    assert record.npi is None
    assert record.raw["NPI"] == "123456789"
    assert record.date_of_birth is None


def test_waivers_stay_in_raw(parsed: ParsedList) -> None:
    record = _by_last(parsed, "SPECIMEN")

    assert record.raw["WAIVERDATE"] == "20240227"
    assert record.raw["WVRSTATE"] == "NM"


def test_a_changed_header_rejects_the_file() -> None:
    body = fixture_bytes("oig_leie.csv").replace(b"EXCLTYPE", b"EXCLCODE", 1)

    with pytest.raises(ParseError, match="unexpected header"):
        _parse(body)
