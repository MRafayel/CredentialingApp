from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.md_medicaid import MdMedicaidConnector
from credentialing.connectors.md_medicaid.connector import SANCTION_CODES, action_for, reason_for
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
    source_id="md_medicaid",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=MdMedicaidConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return MdMedicaidConnector(ScriptedFetcher()).parse(builders.md_medicaid(), SNAPSHOT)


def _named(parsed: ParsedList, last: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["LAST NAME/ORGANIZATION"] == last)


def test_the_legend_and_list_date_below_the_data_are_not_records(parsed: ParsedList) -> None:
    assert parsed.rows == 6
    assert len(parsed.records) == 6
    assert parsed.skipped == 0
    assert all("=" not in r.raw["LAST NAME/ORGANIZATION"] for r in parsed.records)


def test_an_empty_first_name_is_an_organization(parsed: ParsedList) -> None:
    record = _named(parsed, "Sampleville Medical Supply Company")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sampleville Medical Supply Company"
    assert record.person_name is None
    assert record.action is ExclusionAction.SANCTION
    assert record.action_raw == "MDH"
    assert record.reason == SANCTION_CODES["MDH"]
    assert record.address == Address(
        line1="146 Sample Drive Ste 100", city="Testtown", state="MD", postal_code="21136"
    )


def test_a_person_with_npi_licence_and_licensing_board_action(parsed: ParsedList) -> None:
    record = _named(parsed, "Testperson")

    assert record.person_name == PersonName(last="Testperson", first="Avery")
    assert record.npi == builders.NPI_A
    assert record.license == License(number="D50296", state="MD")
    assert record.effective_date == date(2019, 12, 31)
    assert record.raw["TYPE OF ENTITY/PROFESSION"] == "MD"


def test_federal_codes_are_exclusions_and_a_second_npi_stays_raw(parsed: ParsedList) -> None:
    hhs = _named(parsed, "Exampledoc")
    combined = _named(parsed, "Mockperson")

    assert hhs.action is ExclusionAction.EXCLUSION
    assert hhs.npi == builders.NPI_B
    assert builders.NPI_C in hhs.raw["NPI"]
    assert combined.action is ExclusionAction.EXCLUSION
    assert combined.reason == f"{SANCTION_CODES['LB']}; {SANCTION_CODES['HHS']}"
    assert combined.address is None


def test_unexplained_codes_blank_codes_and_bad_npis(parsed: ParsedList) -> None:
    unexplained = _named(parsed, "Samplewoman")
    blank = _named(parsed, "Fakename")

    assert unexplained.action_raw == "MFVVU-A"
    assert unexplained.reason is None
    assert unexplained.address == Address(
        line1="699 Test Drive", city="Glen Sample", postal_code="21060"
    )
    assert blank.action is ExclusionAction.SANCTION
    assert blank.action_raw is None
    assert blank.npi is None
    assert blank.effective_date is None


@pytest.mark.parametrize(
    ("code", "action"),
    [
        ("OIG", ExclusionAction.EXCLUSION),
        ("HHS", ExclusionAction.EXCLUSION),
        ("LB/HHS", ExclusionAction.EXCLUSION),
        ("LB", ExclusionAction.SANCTION),
        ("MFVVU", ExclusionAction.SANCTION),
        (None, ExclusionAction.SANCTION),
    ],
)
def test_action_for(code: str | None, action: ExclusionAction) -> None:
    assert action_for(code) is action


def test_reason_for_needs_every_part_explained() -> None:
    assert reason_for("F") == SANCTION_CODES["F"]
    assert reason_for("LC") is None
    assert reason_for("LB/LC") is None
    assert reason_for(None) is None
