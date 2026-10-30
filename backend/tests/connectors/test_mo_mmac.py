from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.mo_mmac import MoMmacConnector
from credentialing.connectors.mo_mmac.connector import LANDING_URL, parse_license
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, License, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders, fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="mo_mmac",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url="https://mmac.mo.gov/wp-content/uploads/sanction-list-2026-09-30.xlsx",
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return MoMmacConnector(ScriptedFetcher()).parse(builders.mo_mmac(), SNAPSHOT)


def _record(parsed: ParsedList, name: str) -> list[ExclusionRecord]:
    return [r for r in parsed.records if r.raw["PROVIDER NAME"] == name]


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 7
    assert len(parsed.records) == 6
    assert parsed.skipped == 0
    assert parsed.unreadable == {}
    assert all(r.action is ExclusionAction.TERMINATION for r in parsed.records)


def test_rows_differing_only_in_provider_type_are_one_record(parsed: ParsedList) -> None:
    [record] = _record(parsed, "SAMPLE CARE SERVICES LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "SAMPLE CARE SERVICES LLC"
    assert record.raw["PROV TYPE/SPECIALTY"] == "26-CDS; 27-REASSESS"
    assert record.npi == builders.NPI_A
    assert record.license is None
    assert record.reason == "FAILURE TO USE EVV"
    assert record.effective_date == date(2024, 6, 12)


def test_rows_with_a_licence_per_type_stay_separate(parsed: ParsedList) -> None:
    records = _record(parsed, "MOCKVILLE ADULT DAY CENTER")

    assert sorted(r.license.number for r in records if r.license) == ["532", "756"]


def test_a_person_with_a_text_date_and_another_states_licence(parsed: ParsedList) -> None:
    [record] = _record(parsed, "TESTPERSON, AVERY J JR")

    assert record.person_name == PersonName(
        last="TESTPERSON", first="AVERY", middle="J", suffix="JR"
    )
    assert record.effective_date == date(2019, 2, 28)
    assert record.license == License(number="0412345", state="KS")
    assert record.raw["LETTER DATE"] == "2019-01-29"


def test_a_comma_less_name_is_an_organization_unless_it_ends_in_a_credential(
    parsed: ParsedList,
) -> None:
    [business] = _record(parsed, "A MOCK CDS")
    [person] = _record(parsed, "JORDAN K EXAMPLEDOC MD")

    assert business.entity_kind is EntityKind.ORGANIZATION
    assert business.npi is None
    assert person.person_name == PersonName(
        last="EXAMPLEDOC", first="JORDAN", middle="K", suffix="MD"
    )
    assert person.license == License(number="2007012345", state="MO")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("N/A", None),
        ("R6056", License(number="R6056", state="MO")),
        ("IL-036112430", License(number="036112430", state="IL")),
        ("MO 01137", License(number="01137", state="MO")),
        ("RN 12345", License(number="RN 12345", state="MO")),
    ],
)
def test_licence_prefixes(text: str, expected: License | None) -> None:
    assert parse_license(text) == expected


def test_the_workbook_link_is_found_on_the_landing_page() -> None:
    page = fixture_bytes("mo_mmac_landing.html").decode()

    link = MoMmacConnector(ScriptedFetcher()).find_link(page)

    assert link == "https://mmac.mo.gov/wp-content/uploads/sanction-list-2026-09-30.xlsx"
    assert MoMmacConnector.landing_url == LANDING_URL
