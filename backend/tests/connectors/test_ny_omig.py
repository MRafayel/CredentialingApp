from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.ny_omig import NyOmigConnector
from credentialing.connectors.ny_omig.connector import is_entity_type
from credentialing.domain.enums import EntityKind, Outcome
from credentialing.domain.models import ExclusionRecord, License, PersonName, SourceSnapshot
from tests.connectors.fixtures import fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="ny_omig",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=NyOmigConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    connector = NyOmigConnector(ScriptedFetcher())
    return connector.parse(fixture_bytes("ny_omig.txt"), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["PROVIDER_NAME"] == text)


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 13
    assert len(parsed.records) == 12
    assert parsed.skipped == 1
    assert parsed.unreadable == {"EXCLUSION_EFFECTIVE_DATE": 1}


def test_an_individual_with_credential_license_and_npi(parsed: ParsedList) -> None:
    record = _named(parsed, "TESTPERSON AVERY J  MD")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(
        last="TESTPERSON", first="AVERY", middle="J", suffix="MD"
    )
    assert record.license == License(number="123456", state="NY")
    assert record.npi == "1000000038"
    assert record.effective_date == date(2014, 3, 1)
    assert record.raw["PROVIDER_TYPE"] == "PHYSICIAN"


def test_aliases(parsed: ParsedList) -> None:
    assert _named(parsed, "SAMPLEMAN JORDAN AKA EXAMPLEMAN JORDAN").aliases == (
        "EXAMPLEMAN JORDAN",
    )
    clinic = _named(parsed, "FAKENAME RILEY DBA FAKENAME FAMILY CLINIC")
    assert clinic.person_name == PersonName(last="FAKENAME", first="RILEY")
    assert clinic.aliases == ("FAKENAME FAMILY CLINIC",)


def test_organizations_by_name_and_by_provider_type(parsed: ParsedList) -> None:
    pharmacy = _named(parsed, "SAMPLEVILLE PHARMACY INC")
    assert pharmacy.entity_kind is EntityKind.ORGANIZATION
    assert pharmacy.organization_name == "SAMPLEVILLE PHARMACY INC"
    assert pharmacy.license is None

    hinted = _named(parsed, "MOCKWELL QUINN")
    assert hinted.entity_kind is EntityKind.ORGANIZATION

    aide = _named(parsed, "EXAMPLE CASEY")
    assert aide.entity_kind is EntityKind.INDIVIDUAL
    assert aide.effective_date is None


def test_particles_and_short_npis(parsed: ParsedList) -> None:
    record = _named(parsed, "DE LA MOCK MARIA")

    assert record.person_name == PersonName(last="DE LA MOCK", first="MARIA")
    assert record.npi is None
    assert record.raw["NPI_Number"] == "123456789"


def test_a_physician_corporation_is_the_physician(parsed: ParsedList) -> None:
    record = _named(parsed, "SAMPLEPERSON AVERY MD PC")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="SAMPLEPERSON", first="AVERY", suffix="MD PC")
    assert record.aliases == ("SAMPLEPERSON AVERY MD PC",)


def test_a_person_role_at_an_entity_stays_a_person(parsed: ParsedList) -> None:
    record = _named(parsed, "CLINICWORKER JAMIE")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="CLINICWORKER", first="JAMIE")


def test_entity_types_and_business_names_are_organizations(parsed: ParsedList) -> None:
    for name in ("SAMPLE MEDICAL ASSOCIATES", "ACME MEDICAL PC"):
        record = _named(parsed, name)
        assert record.entity_kind is EntityKind.ORGANIZATION
        assert record.organization_name == name


@pytest.mark.parametrize(
    ("provider_type", "expected"),
    [
        ("Pharmacy", True),
        ("Dental Practice", True),
        ("Home Care Agency", True),
        ("Sober Home", True),
        ("Medical Clinic", True),
        ("Clinic Employee", False),
        ("Pharmacy Technician", False),
        ("Laboratory Technician", False),
        ("Hospital Clerk", False),
        ("Owner-Pharmacy", False),
        ("Owner-Home Care Agency", False),
        ("Physician Practice", False),
        ("Physician", False),
        ("", False),
    ],
)
def test_is_entity_type(provider_type: str, expected: bool) -> None:
    assert is_entity_type(provider_type) is expected
