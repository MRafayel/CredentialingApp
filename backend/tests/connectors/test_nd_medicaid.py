from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.nd_medicaid import NdMedicaidConnector
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)
from tests.connectors.fixtures import builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="nd_medicaid",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=NdMedicaidConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return NdMedicaidConnector(ScriptedFetcher()).parse(builders.nd_medicaid(), SNAPSHOT)


def _named(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["Provider Name"] == text)


def test_the_header_is_found_below_the_merged_title(parsed: ParsedList) -> None:
    assert parsed.rows == 4
    assert len(parsed.records) == 4
    assert parsed.skipped == 0


def test_a_qualified_service_provider_with_a_full_address(parsed: ParsedList) -> None:
    record = _named(parsed, "Testperson, Avery")

    assert record.person_name == PersonName(last="Testperson", first="Avery")
    assert record.npi is None
    assert record.license is None
    assert record.address == Address(
        line1="2006 Sample St", city="Sampleville", state="ND", postal_code="58201"
    )
    assert record.action is ExclusionAction.EXCLUSION
    assert record.reason == "Reciprocal to OIG Exclusion"
    assert record.effective_date == date(2024, 8, 20)
    assert record.raw["Provider Type"] == "Qualified Service Provider"


def test_a_former_last_name_becomes_a_full_name_alias(parsed: ParsedList) -> None:
    record = _named(parsed, "Exampleperson (aka Mockname), Jordan")

    assert record.person_name == PersonName(last="Exampleperson", first="Jordan")
    assert record.aliases == ("Mockname, Jordan",)
    assert record.npi == builders.NPI_A
    assert record.license == License(number="R12345", state="ND")


def test_organizations_zip_plus_four_and_nine_digit_npis(parsed: ParsedList) -> None:
    organization = _named(parsed, "Smiling Sample Care")
    nine_digits = _named(parsed, "Sampleman, Riley Jr")

    assert organization.entity_kind is EntityKind.ORGANIZATION
    assert organization.address is not None
    assert organization.address.postal_code == "56721-1818"
    assert nine_digits.npi is None
    assert nine_digits.raw["NPI"] == "178037189"
    assert nine_digits.person_name == PersonName(last="Sampleman", first="Riley", suffix="Jr")
    assert nine_digits.address is not None
    assert nine_digits.address.postal_code == "58501-1234"
