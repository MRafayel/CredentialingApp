from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.vt_medicaid import VtMedicaidConnector
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="vt_medicaid",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=VtMedicaidConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return VtMedicaidConnector(ScriptedFetcher()).parse(builders.vt_medicaid(), SNAPSHOT)


def _by_id(parsed: ParsedList, provider_id: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.state_provider_id == provider_id)


def test_the_table_ends_at_the_blank_row_above_the_footnotes(parsed: ParsedList) -> None:
    assert parsed.rows == 4
    assert len(parsed.records) == 4
    assert parsed.skipped == 0


def test_a_padded_name_without_a_space_after_the_comma(parsed: ParsedList) -> None:
    record = _by_id(parsed, "0001926")

    assert record.person_name == PersonName(
        last="TESTPERSON", first="AVERY", middle="J", suffix="DMD"
    )
    assert record.npi == builders.NPI_A
    assert record.address == Address(city="SAMPLEVILLE", state="VT")
    assert record.raw["Provider Type"] == "DENTIST"


def test_the_second_state_column_is_the_reason_code(parsed: ParsedList) -> None:
    record = _by_id(parsed, "0001926")

    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "22"
    assert record.reason == "TERMINATED DUE TO SANCTIONS"
    assert record.raw["State"] == "VT"
    assert record.raw["State (2)"] == "22"


def test_the_placeholder_end_date_means_open_ended(parsed: ParsedList) -> None:
    record = _by_id(parsed, "0001926")

    assert record.effective_date == date(2020, 2, 12)
    assert record.end_date is None


def test_a_reinstatement_date_ends_the_record(parsed: ParsedList) -> None:
    record = _by_id(parsed, "0002002")

    assert record.end_date == date(2023, 5, 1)
    assert record.person_name == PersonName(last="EXAMPLE", first="JORDAN", suffix="LCMHC")


def test_names_without_a_comma_are_organizations(parsed: ParsedList) -> None:
    lab = _by_id(parsed, "0002001")
    clinic = _by_id(parsed, "0002003")

    assert lab.entity_kind is EntityKind.ORGANIZATION
    assert lab.organization_name == "SAMPLE LAB WERKS INC"
    assert clinic.entity_kind is EntityKind.ORGANIZATION
    assert clinic.effective_date == date(2024, 6, 30)


def test_a_second_npi_in_the_cell_is_a_related_npi(parsed: ParsedList) -> None:
    clinic = _by_id(parsed, "0002003")
    single = _by_id(parsed, "0001926")

    assert clinic.npi == builders.NPI_C
    assert clinic.related_npis == (builders.NPI_D,)
    assert single.related_npis == ()
