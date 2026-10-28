from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.ia_medicaid import IaMedicaidConnector
from credentialing.connectors.ia_medicaid.connector import entity_kind_for
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, License, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="ia_medicaid",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=IaMedicaidConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return IaMedicaidConnector(ScriptedFetcher()).parse(builders.ia_medicaid(), SNAPSHOT)


def _named(parsed: ParsedList, name: str) -> ExclusionRecord:
    return next(
        r for r in parsed.records if name in (r.raw["Last Name"], r.raw["Legal Business Name"])
    )


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 5
    assert len(parsed.records) == 5
    assert parsed.skipped == 0
    assert parsed.unreadable == {"Effective Date": 1}


def test_an_individual_suspended_indefinitely_with_an_iowa_licence(parsed: ParsedList) -> None:
    record = _named(parsed, "Testperson")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery Beth")
    assert record.action is ExclusionAction.SUSPENSION
    assert record.action_raw == "Participation Suspension"
    assert record.effective_date == date(2021, 3, 18)
    assert record.end_date is None
    assert record.reason == "441 IAC 79.2(2)(a)"
    assert record.license == License(number="P57910", state="IA", kind="LPN")
    assert record.raw["Specialty"] == "LPN"


def test_an_organization_with_an_end_date(parsed: ParsedList) -> None:
    record = _named(parsed, "Sample Pharmacy LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sample Pharmacy LLC"
    assert record.npi == builders.NPI_A
    assert record.related_npis == (builders.NPI_D,)
    assert record.action is ExclusionAction.TERMINATION
    assert record.end_date == date(2024, 1, 1)


def test_a_typo_in_the_enrollment_type_and_a_medicaid_id(parsed: ParsedList) -> None:
    record = _named(parsed, "Exampledoc")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.npi is None
    assert record.related_npis == (builders.NPI_B,)
    assert record.state_provider_id == "X000000111"
    assert record.aliases == ("Exampledoc Clinic",)
    assert record.action is ExclusionAction.EXCLUSION
    assert record.end_date is None
    assert record.raw["Affiliated NPI"] == builders.NPI_B


def test_short_numbers_and_words_in_date_columns(parsed: ParsedList) -> None:
    record = _named(parsed, "Mockperson")

    assert record.npi is None
    assert record.state_provider_id is None
    assert record.raw["NPI"] == "171092119"
    assert record.effective_date is None
    assert record.end_date is None


@pytest.mark.parametrize(
    ("enrollment", "business", "last", "expected"),
    [
        ("Individual", None, "Mock", EntityKind.INDIVIDUAL),
        ("Indvidual", None, "Mock", EntityKind.INDIVIDUAL),
        ("individual", "Clinic", "Mock", EntityKind.INDIVIDUAL),
        ("Organization", "Clinic", None, EntityKind.ORGANIZATION),
        ("Organisation", "Clinic", None, EntityKind.ORGANIZATION),
        ("Organization", None, "Mock", EntityKind.INDIVIDUAL),
        ("", "Clinic", None, EntityKind.ORGANIZATION),
        (None, None, None, EntityKind.UNKNOWN),
    ],
)
def test_entity_kind_for(
    enrollment: str | None, business: str | None, last: str | None, expected: EntityKind
) -> None:
    assert entity_kind_for(enrollment, business, last) is expected


def test_an_affiliated_value_that_is_no_npi_stays_raw(parsed: ParsedList) -> None:
    record = _named(parsed, "Samplewoman")

    assert record.npi == builders.NPI_C
    assert record.related_npis == ()
    assert record.raw["Affiliated NPI"] == "X000000222"
