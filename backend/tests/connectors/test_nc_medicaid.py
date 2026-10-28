from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.nc_medicaid import NcMedicaidConnector
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="nc_medicaid",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=NcMedicaidConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return NcMedicaidConnector(ScriptedFetcher()).parse(builders.nc_medicaid(), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> list[ExclusionRecord]:
    return [r for r in parsed.records if r.raw["EXCLUDED ENTITY"] == text]


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 9
    assert len(parsed.records) == 8
    assert parsed.skipped == 1
    assert parsed.unreadable == {"EFFECTIVE DATE": 1}


def test_an_organization_with_its_owner_kept_out_of_the_names(parsed: ParsedList) -> None:
    first, second = _named(parsed, "SAMPLEVILLE CARE SERVICES LLC")

    assert first.entity_kind is EntityKind.ORGANIZATION
    assert first.organization_name == "SAMPLEVILLE CARE SERVICES LLC"
    assert first.npi == builders.NPI_A
    assert first.aliases == ()
    assert first.raw["OWNERSHIP"] == "AVERY\xa0J TESTOWNER"
    assert first.address == Address(city="SAMPLEVILLE", state="NC", postal_code="28501")
    assert first.action is ExclusionAction.EXCLUSION
    assert first.reason == "FEDERAL"
    assert first.effective_date == date(2026, 2, 19)
    assert first.record_hash == second.record_hash


def test_people_are_written_first_name_first(parsed: ParsedList) -> None:
    (person,) = _named(parsed, "JORDAN LEE EXAMPLEPERSON")
    (particle,) = _named(parsed, "QUINN DE LA MOCK")

    assert person.person_name == PersonName(last="EXAMPLEPERSON", first="JORDAN", middle="LEE")
    assert person.reason == "OIG/STATE"
    assert particle.person_name == PersonName(last="DE LA MOCK", first="QUINN")


def test_identifiers_npi_atypical_id_and_placeholder(parsed: ParsedList) -> None:
    (atypical,) = _named(parsed, "MOCK BILLING AGENCY")
    (maiden,) = _named(parsed, "CASEY (MAIDENMOCK) SAMPLEWOMAN")

    assert atypical.npi is None
    assert atypical.state_provider_id == "50345490"
    assert maiden.npi is None
    assert maiden.state_provider_id is None


def test_text_dates_maiden_names_and_entities_without_business_words(
    parsed: ParsedList,
) -> None:
    (maiden,) = _named(parsed, "CASEY (MAIDENMOCK) SAMPLEWOMAN")
    (practice,) = _named(parsed, "SAMPLE EAR NOSE AND THROAT")

    assert maiden.effective_date == date(2021, 3, 15)
    assert maiden.person_name == PersonName(last="SAMPLEWOMAN", first="CASEY")
    assert maiden.aliases == ("MAIDENMOCK",)
    assert practice.entity_kind is EntityKind.ORGANIZATION
