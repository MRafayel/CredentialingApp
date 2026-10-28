from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.pa_medicheck import PaMedicheckConnector
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, License, PersonName, SourceSnapshot
from tests.connectors.fixtures import fixture_bytes
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW, make_npi

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="pa_medicheck",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=PaMedicheckConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return PaMedicheckConnector(ScriptedFetcher()).parse(
        fixture_bytes("pa_medicheck.csv"), SNAPSHOT
    )


def _rows(parsed: ParsedList, name: str) -> list[ExclusionRecord]:
    return [r for r in parsed.records if r.raw["ProviderName"] == name]


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 9
    assert len(parsed.records) == 9
    assert parsed.skipped == 0
    assert parsed.unreadable == {"BeginDate": 1}


def test_a_history_keeps_one_record_per_action(parsed: ParsedList) -> None:
    precluded, reinstated = _rows(parsed, "Testperson, Avery J (RN)")

    assert precluded.person_name == PersonName(
        last="Testperson", first="Avery", middle="J", suffix="RN"
    )
    assert precluded.npi == make_npi("100000011")
    assert precluded.license == License(number="RN123456", state="PA")
    assert precluded.action is ExclusionAction.PRECLUSION
    assert precluded.effective_date == date(2003, 10, 17)
    assert precluded.end_date is None
    assert precluded.raw["CAO"] == "Out of State (CA)"
    assert reinstated.action is ExclusionAction.REINSTATEMENT
    assert reinstated.effective_date == date(2010, 5, 1)
    assert precluded.record_hash != reinstated.record_hash


def test_end_dates_and_the_far_future_placeholder(parsed: ParsedList) -> None:
    (ended,) = _rows(parsed, "Exampleperson, Jordan")
    (permanent,) = _rows(parsed, "Mockdoc, Riley Jr (MD)")

    assert ended.end_date == date(2000, 6, 7)
    assert permanent.action is ExclusionAction.PRECLUSION
    assert permanent.action_raw == "Permanent Preclusion"
    assert permanent.end_date is None
    assert permanent.raw["EndDate"] == "12/31/2299 0:00:00"


def test_title_suffix_alternate_name_and_business(parsed: ParsedList) -> None:
    (record,) = _rows(parsed, "Mockdoc, Riley Jr (MD)")

    assert record.person_name == PersonName(last="Mockdoc", first="Riley", suffix="Jr MD")
    assert record.aliases == ("Mockdoc Family Medicine", "Mockdoctor, Riley")


def test_organizations_from_the_business_column_or_the_provider_name(parsed: ParsedList) -> None:
    (pharmacy,) = _rows(parsed, "Sample Pharmacy Inc. a/k/a Mock Drugs")
    (clinic,) = _rows(parsed, "Example Clinic LLC")

    assert pharmacy.entity_kind is EntityKind.ORGANIZATION
    assert pharmacy.organization_name == "Sample Pharmacy Inc."
    assert pharmacy.aliases == ("Mock Drugs",)
    assert pharmacy.raw["NBR_FEIN"] == "12-3456789"
    assert clinic.entity_kind is EntityKind.ORGANIZATION
    assert clinic.action is ExclusionAction.TERMINATION
    assert clinic.action_raw == "Terminated-No Cause"


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("Fakename, Casey", ExclusionAction.OTHER),
        ("Samplewoman, Quinn", ExclusionAction.OTHER),
        ("Mockperson, Sam", ExclusionAction.SUSPENSION),
    ],
)
def test_statuses(parsed: ParsedList, name: str, action: ExclusionAction) -> None:
    (record,) = _rows(parsed, name)

    assert record.action is action
