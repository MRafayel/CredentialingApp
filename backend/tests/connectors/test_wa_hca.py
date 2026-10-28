from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.framework.values import classify_action
from credentialing.connectors.wa_hca import WaHcaConnector
from credentialing.connectors.wa_hca.connector import ACTION_KEYWORDS, provider_one_id
from credentialing.domain.enums import EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import ExclusionRecord, License, PersonName, SourceSnapshot
from tests.connectors.fixtures import builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="wa_hca",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url=WaHcaConnector.download_url,
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return WaHcaConnector(ScriptedFetcher()).parse(builders.wa_hca(), SNAPSHOT)


def _starting(parsed: ParsedList, text: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.raw["Name"].startswith(text))


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 5
    assert len(parsed.records) == 5
    assert parsed.skipped == 0


def test_a_person_with_credential_licence_and_npi(parsed: ParsedList) -> None:
    record = _starting(parsed, "Testperson, Avery")

    assert record.person_name == PersonName(
        last="Testperson", first="Avery", middle="M", suffix="ARNP"
    )
    assert record.license == License(number="AP60000001", state="WA")
    assert record.npi == builders.NPI_A
    assert record.action is ExclusionAction.SUSPENSION
    assert record.action_raw == "License Suspended"
    assert record.effective_date == date(2023, 9, 21)


def test_several_names_in_one_cell_are_the_party_and_its_aliases(parsed: ParsedList) -> None:
    record = _starting(parsed, "Sample Dentures NW")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Sample Dentures NW"
    assert record.aliases == ("Mock Dental and Denture", "Exampleowner, Jordan L. DN")
    assert record.npi == builders.NPI_B
    assert builders.NPI_C in record.raw["NPI # or P1 #"]
    assert record.action is ExclusionAction.TERMINATION


def test_a_providerone_id_and_a_dba_line(parsed: ParsedList) -> None:
    record = _starting(parsed, "Mockdoc, Riley")

    assert record.npi is None
    assert record.state_provider_id == "1027986"
    assert record.aliases == ("Sample Pathology Labs",)
    assert record.action is ExclusionAction.EXCLUSION


def test_text_dates_invalid_npis_and_second_licences(parsed: ParsedList) -> None:
    invalid = _starting(parsed, "Fakename, Casey")
    two_licences = _starting(parsed, "Example, Quinn")

    assert invalid.effective_date == date(2020, 10, 1)
    assert invalid.npi is None
    assert invalid.state_provider_id is None
    assert invalid.action is ExclusionAction.SANCTION
    assert two_licences.license == License(number="PA001", state="WA")
    assert two_licences.person_name is not None
    assert two_licences.person_name.suffix == "PA-C"


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("OIG Exclusion", ExclusionAction.EXCLUSION),
        ("Manditory Exclusion from Medicaid", ExclusionAction.EXCLUSION),
        ("Terminate Medicaid Authority", ExclusionAction.TERMINATION),
        ("Terminated by CMS", ExclusionAction.TERMINATION),
        ("License Suspened", ExclusionAction.SUSPENSION),
        ("License Expired/Suspended", ExclusionAction.SUSPENSION),
        ("License Revoked", ExclusionAction.SANCTION),
        ("Expired License", ExclusionAction.SANCTION),
        ("License Surrendered", ExclusionAction.SANCTION),
        ("Medicaid Fraud Conviction", ExclusionAction.SANCTION),
    ],
)
def test_action_keywords(text: str, action: ExclusionAction) -> None:
    assert classify_action(text, ACTION_KEYWORDS) is action


def test_provider_one_id() -> None:
    assert provider_one_id("P1 - 1027986") == "1027986"
    assert provider_one_id("p1-2052429") == "2052429"
    assert provider_one_id(builders.NPI_A) is None
    assert provider_one_id(None) is None
