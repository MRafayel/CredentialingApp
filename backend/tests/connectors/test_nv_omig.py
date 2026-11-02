from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.nv_omig import NvOmigConnector
from credentialing.connectors.nv_omig.connector import (
    CAPABILITIES,
    CONTROLLING,
    LANDING_URL,
    NAME,
    TERMINATION,
    split_names,
)
from credentialing.domain.enums import ChangeSignal, EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders, fixture_bytes, nv_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="nv_omig",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url="https://www.omig.nv.gov/siteassets/press-releases/master-exclusions-10.21.2026.pdf",
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return NvOmigConnector(ScriptedFetcher()).parse(nv_builders.nv_omig(), SNAPSHOT)


def _record(parsed: ParsedList, starts_with: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw[NAME].startswith(starts_with)]
    return record


def test_counts_across_both_pages(parsed: ParsedList) -> None:
    assert parsed.rows == 12
    assert parsed.skipped == 1
    assert parsed.merged == 1
    assert len(parsed.records) == 10
    assert parsed.unreadable == {}


def test_capabilities_record_the_politeness_settings() -> None:
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)
    assert CAPABILITIES.poll_interval == timedelta(days=7)
    assert CAPABILITIES.jurisdictions == frozenset({"NV"})
    assert CAPABILITIES.download_url is None


def test_a_federal_row_is_an_oig_exclusion_ending_on_reinstatement(parsed: ParsedList) -> None:
    record = _record(parsed, "Avery")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(
        last="Testperson", first="Avery", middle="Q.", suffix="MD"
    )
    assert record.npi == builders.NPI_A
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "Federal"
    assert record.effective_date == date(2019, 2, 20)
    assert record.end_date == date(2024, 5, 1)
    assert record.reason is None
    assert record.raw["Provider Type"] == "20"


def test_a_federal_row_without_oig_dates_uses_the_termination_date(parsed: ParsedList) -> None:
    record = _record(parsed, "Jordan")

    assert record.action is ExclusionAction.EXCLUSION
    assert record.effective_date == date(2015, 6, 30)
    assert record.end_date is None
    assert record.npi is None


def test_an_entity_named_as_a_legal_entity_is_an_organization_with_its_owners(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, nv_builders.ENTITY)

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == nv_builders.ENTITY
    assert record.related_parties == ("Riley Mockwell", "Casey Fakename")
    assert record.aliases == ()
    assert record.raw[CONTROLLING] == "Riley Mockwell; Casey Fakename"
    assert record.npi == builders.NPI_B
    assert record.related_npis == ()
    assert record.state_provider_id == nv_builders.MEDICAID_ID_A
    assert record.action is ExclusionAction.SANCTION
    assert record.action_raw == "Tier 2"
    assert record.reason == "10 Years"
    assert record.effective_date == date(2021, 1, 15)
    assert record.end_date == date(2031, 1, 15)


def test_a_controlling_person_names_the_entity_as_a_related_party(parsed: ParsedList) -> None:
    record = _record(parsed, "Riley")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Mockwell", first="Riley")
    assert record.related_parties == (nv_builders.ENTITY, "Casey Fakename")
    assert record.aliases == ()


def test_an_aka_name_and_a_medicaid_id_without_an_npi(parsed: ParsedList) -> None:
    record = _record(parsed, "Morgan")

    assert record.person_name == PersonName(last="Exampleton", first="Morgan")
    assert record.aliases == ("Morgan Otherton",)
    assert record.npi is None
    assert record.state_provider_id == nv_builders.MEDICAID_ID_B
    assert record.action_raw == "Tier 3"
    assert record.reason == "3 years"
    assert record.end_date == date(2025, 11, 2)


def test_a_dba_line_two_dates_and_a_permanent_sanction(parsed: ParsedList) -> None:
    record = _record(parsed, "Sample Care")

    assert record.organization_name == "Sample Care Services LLC"
    assert record.aliases == ("Sample Treatment Center",)
    assert record.related_parties == ("Pat Testowner", "Lee Mockpartner")
    assert record.npi is None
    assert record.state_provider_id is None
    assert record.raw["Sanctioned/Excluded NPI"] == builders.INVALID_TEN_DIGITS
    assert record.effective_date == date(2016, 4, 20)
    assert record.raw[TERMINATION] == "4/20/2016\n11/23/2016"
    assert record.action_raw == "Tier 1"
    assert record.reason == "Permanent"
    assert record.end_date is None


def test_two_tiers_and_a_wrapped_period_note(parsed: ParsedList) -> None:
    record = _record(parsed, "Taylor")

    assert record.npi == builders.NPI_C
    assert record.action_raw == "Tier 2, 3"
    assert record.reason == "10 Years 3 Years * imposed a 10 year sanction"
    assert record.end_date == date(2036, 6, 30)


def test_a_footnote_mark_after_an_end_date_is_ignored(parsed: ParsedList) -> None:
    record = _record(parsed, "Drew")

    assert record.action_raw == "Tier 4"
    assert record.reason == "12 Months"
    assert record.end_date == date(2022, 7, 14)
    assert record.person_name == PersonName(last="Mockford", first="Drew", middle="Q")


def test_rows_without_a_tier(parsed: ParsedList) -> None:
    excluded = _record(parsed, "Jamie")
    unexplained = _record(parsed, "Quinn")

    assert excluded.action is ExclusionAction.EXCLUSION
    assert excluded.action_raw is None
    assert excluded.effective_date == date(2019, 9, 9)
    assert unexplained.action is ExclusionAction.OTHER
    assert unexplained.effective_date is None
    assert unexplained.end_date is None


def test_a_note_in_the_name_column_is_not_a_record(parsed: ParsedList) -> None:
    assert not [r for r in parsed.records if r.raw[NAME].startswith("As of")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        ("Sample Care LLC dba\nSample Center", ("Sample Care LLC", "Sample Center")),
        ("Avery Mock a.k.a. Avery Other", ("Avery Mock", "Avery Other")),
        ("Mock & Sons LLC", ("Mock & Sons LLC",)),
        ("Sample Group, LLC DBA: Sample Home", ("Sample Group, LLC", "Sample Home")),
    ],
)
def test_split_names(text: str, expected: tuple[str, ...]) -> None:
    assert split_names(text) == expected


def test_the_pdf_link_is_found_on_the_landing_page() -> None:
    page = fixture_bytes("nv_omig_landing.html").decode()

    link = NvOmigConnector(ScriptedFetcher()).find_link(page)

    assert link == "/siteassets/press-releases/master-exclusions-10.21.2026.pdf"
    assert NvOmigConnector.landing_url == LANDING_URL
