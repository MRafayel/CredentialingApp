from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.nj_treasury import NjTreasuryConnector
from credentialing.connectors.nj_treasury.connector import without_profession
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW, make_npi

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="nj_treasury",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=NjTreasuryConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return NjTreasuryConnector(ScriptedFetcher()).parse(fixture_bytes("nj_treasury.txt"), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if text in (r.raw["Individual Name"], r.raw["Firm Name"]))


def test_the_stray_line_and_the_row_without_an_action_are_skipped(parsed: ParsedList) -> None:
    assert parsed.rows == 8
    assert len(parsed.records) == 6
    assert parsed.skipped == 2
    assert parsed.unreadable == {"Effective Date": 2}


def test_an_individual_with_credential_npi_and_address(parsed: ParsedList) -> None:
    record = _named(parsed, "TESTPERSON, AVERY J. (MD)")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(
        last="TESTPERSON", first="AVERY", middle="J.", suffix="MD"
    )
    assert record.npi == make_npi("100000001")
    assert record.address == Address(
        line1="12 SAMPLE STREET", city="SAMPLEVILLE", state="NJ", postal_code="08260-1234"
    )
    assert record.action is ExclusionAction.DISQUALIFICATION
    assert record.action_raw == "DISQUALIFICATION"
    assert record.reason == "Other"
    assert record.effective_date == date(2025, 12, 6)
    assert record.end_date is None
    assert record.related_parties == ()


def test_the_profession_is_not_an_alias_and_the_vendor_id_is_never_stored(
    parsed: ParsedList,
) -> None:
    record = _named(parsed, "MOCKFIELD, JORDAN  AKA EXAMPLE, JORDAN (CHHA)")

    assert record.person_name == PersonName(last="MOCKFIELD", first="JORDAN")
    assert record.aliases == ("EXAMPLE, JORDAN",)
    assert record.action is ExclusionAction.SUSPENSION
    assert record.reason == "Criminal Offense"
    assert record.end_date is None
    assert record.raw["Expiration Date"] == "DECEASED"
    for listed in parsed.records:
        assert "Vendor ID" not in listed.raw
        assert "Unused 23" not in listed.raw
        assert "99999999901" not in listed.raw.values()


def test_a_firm_row_with_two_npis(parsed: ParsedList) -> None:
    record = _named(parsed, "SAMPLEVILLE MEDICAL SUPPLY LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "SAMPLEVILLE MEDICAL SUPPLY LLC"
    assert record.npi == make_npi("100000002")
    assert record.related_npis == (make_npi("100000003"),)
    assert record.address == Address(
        line1="500 MOCK ROAD", city="SAMPLEVILLE", state="NJ", postal_code="07104"
    )
    assert record.action is ExclusionAction.DEBARMENT
    assert record.end_date == date(2026, 7, 15)


def test_a_row_with_both_names_is_the_individuals_with_the_firm_related(
    parsed: ParsedList,
) -> None:
    record = _named(parsed, "EXAMPLEPERSON, RILEY (RPH)")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="EXAMPLEPERSON", first="RILEY", suffix="RPH")
    assert record.related_parties == ("SAMPLE DRUGS INC",)
    assert record.npi == make_npi("100000004")
    assert record.address == Address(
        line1="9 FAKE PLAZA", city="MOCKVILLE", state="NJ", postal_code="07726"
    )
    assert record.reason == "Antitrust / Anti-Kickback"
    assert record.end_date is None


def test_impossible_dates_are_unreadable_not_guessed(parsed: ParsedList) -> None:
    typo = _named(parsed, "SAMPLETON, CASEY (PHARM TECH)")
    far_future = _named(parsed, "FAKENAME, QUINN M. (LPN)")

    assert typo.person_name == PersonName(last="SAMPLETON", first="CASEY")
    assert typo.effective_date is None
    assert typo.raw["Effective Date"] == "5/222024"
    assert far_future.effective_date is None
    assert far_future.end_date == date(2016, 4, 4)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("SMITH, MARY (MD)", "SMITH, MARY (MD)"),
        ("SMITH, MARY (CHHA)", "SMITH, MARY"),
        ("SMITH, MARY (RN/LPN)", "SMITH, MARY"),
        ("SMITH, MARY (MD) (OWNER)", "SMITH, MARY (MD)"),
        ("SMITH, MARY (PHARM TECH))", "SMITH, MARY"),
        ("SMITH (JONES), MARY", "SMITH (JONES), MARY"),
        ("SMITH, MARY", "SMITH, MARY"),
    ],
)
def test_without_profession(name: str, expected: str) -> None:
    assert without_profession(name) == expected
