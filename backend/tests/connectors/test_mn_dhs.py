from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.http import FetchFailure
from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.mn_dhs_individuals import MnDhsIndividualsConnector
from credentialing.connectors.mn_dhs_individuals.connector import COLUMNS as IND_COLUMNS
from credentialing.connectors.mn_dhs_organizations import MnDhsOrganizationsConnector
from credentialing.connectors.mn_dhs_organizations.connector import COLUMNS as ORG_COLUMNS
from credentialing.domain.enums import ChangeSignal, EntityKind, ExclusionAction, Outcome
from credentialing.domain.models import Address, ExclusionRecord, PersonName, SourceSnapshot
from tests.connectors.fixtures import mn_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW


def _snapshot(source_id: str, url: str) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(), source_id=source_id, fetched_at=NOW, outcome=Outcome.COMPLETED, source_url=url
    )


@pytest.fixture(scope="module")
def individuals() -> ParsedList:
    snapshot = _snapshot("mn_dhs_individuals", MnDhsIndividualsConnector.download_url)
    return MnDhsIndividualsConnector(ScriptedFetcher()).parse(
        mn_builders.mn_dhs_individuals(), snapshot
    )


@pytest.fixture(scope="module")
def organizations() -> ParsedList:
    snapshot = _snapshot("mn_dhs_organizations", MnDhsOrganizationsConnector.download_url)
    return MnDhsOrganizationsConnector(ScriptedFetcher()).parse(
        mn_builders.mn_dhs_organizations(), snapshot
    )


def _person(parsed: ParsedList, last: str) -> ExclusionRecord:
    return next(r for r in parsed.records if r.person_name and r.person_name.last == last)


def test_the_fixtures_use_the_connectors_columns() -> None:
    assert mn_builders.INDIVIDUAL_COLUMNS == IND_COLUMNS
    assert mn_builders.ORGANIZATION_COLUMNS == ORG_COLUMNS


def test_individual_counts(individuals: ParsedList) -> None:
    assert individuals.rows == 4
    assert len(individuals.records) == 3
    assert individuals.skipped == 1
    assert individuals.unreadable == {"Effective date of exclusion": 1}


def test_a_direct_support_worker_without_an_address(individuals: ParsedList) -> None:
    record = _person(individuals, "Testperson")

    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.person_name == PersonName(last="Testperson", first="Avery", middle="J")
    assert record.address is None
    assert record.npi is None
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "Medicaid authority"
    assert record.reason == "Medicaid authority"
    assert record.effective_date == date(2024, 3, 15)
    assert record.raw["Provider type Description"] == "Direct Support Worker"


def test_an_address_and_an_excel_date(individuals: ParsedList) -> None:
    record = _person(individuals, "Sampleton")

    assert record.address == Address(
        line1="100 Example Ave",
        line2="Suite 2",
        city="Sampleville",
        state="MN",
        postal_code="55101",
    )
    assert record.effective_date == date(2019, 1, 7)
    assert record.action_raw == "Medicare Medicaid exclusion"


def test_a_missing_first_name_and_a_bad_date(individuals: ParsedList) -> None:
    record = _person(individuals, "Mockfield")

    assert record.person_name == PersonName(last="Mockfield", first="")
    assert record.effective_date is None
    assert record.action_raw == "Denied not eligible"


def test_organizations(organizations: ParsedList) -> None:
    assert (organizations.rows, len(organizations.records), organizations.skipped) == (3, 2, 1)
    record = next(r for r in organizations.records if r.organization_name == "Sample Home Care LLC")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.person_name is None
    assert record.address == Address(
        line1="200 Mock St", city="Testburg", state="MN", postal_code="55401-1234"
    )
    assert record.action is ExclusionAction.EXCLUSION
    assert record.action_raw == "MEDICAID AUTHORITY"
    assert record.effective_date == date(2023, 6, 1)


@pytest.mark.parametrize("connector", [MnDhsIndividualsConnector, MnDhsOrganizationsConnector])
def test_polite_weekly_hash_polling(
    connector: type[MnDhsIndividualsConnector | MnDhsOrganizationsConnector],
) -> None:
    capabilities = connector(ScriptedFetcher()).capabilities

    assert capabilities.crawl_delay.total_seconds() == 5
    assert capabilities.change_signal is ChangeSignal.CONTENT_HASH
    assert capabilities.poll_interval.days == 7
    assert capabilities.jurisdictions == frozenset({"MN"})


@pytest.mark.anyio
@pytest.mark.parametrize("connector", [MnDhsIndividualsConnector, MnDhsOrganizationsConnector])
async def test_a_bot_manager_redirect_stays_blocked(
    connector: type[MnDhsIndividualsConnector | MnDhsOrganizationsConnector],
) -> None:
    redirected = FetchFailure(
        outcome=Outcome.BLOCKED,
        status=200,
        note="redirected to validate.perfdrive.com",
        fetched_at=NOW,
    )
    fetcher = ScriptedFetcher(redirected)

    result = await connector(fetcher).fetch(None)

    assert result.snapshot.outcome is Outcome.BLOCKED
    assert result.records is None
    assert "perfdrive" in (result.snapshot.note or "")
    assert fetcher.requests == [(connector.download_url, ChangeSignal.CONTENT_HASH)]
