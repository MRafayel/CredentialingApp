"""Domain rules of the live NPI registry: when to ask it, what its answers conclude, what is
stored, how a roster provider matches its NPI's record, and the structured query."""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    Freshness,
    MatchField,
    MatchLevel,
    NpiStatus,
    Outcome,
    RegistryStatus,
    VerificationMethod,
)
from credentialing.domain.models import (
    License,
    NpiAnswer,
    NpiRecord,
    NpiTaxonomy,
    PersonName,
    SourceSnapshot,
)
from credentialing.domain.policies import (
    DEFAULT_FRESHNESS,
    carry_forward,
    live_check_needed,
    match_provider_to_npi,
    records_to_store,
    registry_status,
)
from credentialing.domain.ports import NpiQuery
from tests.synthetic import (
    NOW,
    individual,
    make_npi,
    npi_organization,
    npi_person,
    npi_stub,
    organization,
)

NPI = make_npi("160000011")
OTHER = make_npi("160000012")


@pytest.mark.parametrize(
    ("requested", "mirrored", "freshness", "needed"),
    [
        (True, True, Freshness.CACHED, True),
        (True, True, Freshness.LIVE, True),
        (False, False, Freshness.CACHED, True),
        (False, False, None, True),
        (False, True, Freshness.STALE, True),
        (False, True, None, True),
        (False, True, Freshness.CACHED, False),
        (False, True, Freshness.LIVE, False),
    ],
)
def test_the_live_registry_is_asked_only_when_needed(
    requested: bool, mirrored: bool, freshness: Freshness | None, needed: bool
) -> None:
    assert live_check_needed(requested=requested, mirrored=mirrored, freshness=freshness) is needed


def test_a_live_answer_is_live_then_cached_for_thirty_days() -> None:
    policy = DEFAULT_FRESHNESS.policy_for(CredentialType.NPI, "nppes_api")
    method = VerificationMethod.OFFICIAL_API

    assert policy.evaluate(NOW, NOW + timedelta(seconds=30), method) is Freshness.LIVE
    assert policy.evaluate(NOW, NOW + timedelta(days=29), method) is Freshness.CACHED
    assert policy.evaluate(NOW, NOW + timedelta(days=31), method) is Freshness.STALE


@pytest.mark.parametrize(
    ("mirrored", "live_found", "status"),
    [
        (None, True, RegistryStatus.ACTIVE),
        ("deactivated", True, RegistryStatus.ACTIVE),
        ("active", True, RegistryStatus.ACTIVE),
        (None, False, RegistryStatus.NOT_FOUND),
        ("deactivated", False, RegistryStatus.DEACTIVATED),
        ("active", False, RegistryStatus.MIRROR_ONLY_ACTIVE),
        (None, None, RegistryStatus.UNKNOWN),
        ("deactivated", None, RegistryStatus.DEACTIVATED),
        ("active", None, RegistryStatus.ACTIVE),
    ],
)
def test_the_registry_status_never_reads_a_missing_answer_as_not_found(
    mirrored: str | None, live_found: bool | None, status: RegistryStatus
) -> None:
    rows = {"active": npi_person(NPI), "deactivated": npi_stub(NPI)}

    assert registry_status(rows[mirrored] if mirrored else None, live_found) is status


def _dated(record: NpiRecord, updated: date | None, **update: object) -> NpiRecord:
    return record.model_copy(update={"last_updated": updated, **update})


def test_an_npi_the_mirror_lacks_is_stored() -> None:
    answer = _dated(npi_person(NPI), date(2026, 1, 3))

    assert records_to_store([answer], {}) == [answer]


def test_a_newer_answer_is_stored_with_the_mirror_history_carried_forward() -> None:
    mirrored = _dated(
        npi_person(NPI),
        date(2025, 1, 1),
        deactivation_date=date(2020, 1, 1),
        reactivation_date=date(2020, 6, 1),
        deactivation_reason="DT",
        replacement_npi=OTHER,
    )
    answer = _dated(npi_person(NPI), date(2026, 1, 3))

    (stored,) = records_to_store([answer], {NPI: mirrored})

    assert stored.last_updated == date(2026, 1, 3)
    assert (stored.deactivation_date, stored.reactivation_date) == (
        date(2020, 1, 1),
        date(2020, 6, 1),
    )
    assert (stored.deactivation_reason, stored.replacement_npi) == ("DT", OTHER)


@pytest.mark.parametrize("mirrored_update", [date(2026, 1, 3), date(2026, 2, 1)])
def test_an_answer_the_mirror_is_as_current_as_is_not_stored(mirrored_update: date) -> None:
    mirrored = _dated(npi_person(NPI), mirrored_update)
    answer = _dated(npi_person(NPI), date(2026, 1, 3))

    assert records_to_store([answer], {NPI: mirrored}) == []


def test_a_mirror_row_without_a_date_counts_as_older() -> None:
    answer = _dated(npi_person(NPI), date(2026, 1, 3))

    assert records_to_store([answer], {NPI: _dated(npi_person(NPI), None)}) == [answer]
    assert records_to_store([_dated(answer, None)], {NPI: _dated(npi_person(NPI), None)}) == []


def test_an_npi_repeated_in_the_answers_is_stored_once() -> None:
    answer = _dated(npi_person(NPI), date(2026, 1, 3))

    assert records_to_store([answer, answer], {}) == [answer]


def test_the_answer_own_dates_win_over_the_mirror() -> None:
    mirrored = _dated(npi_stub(NPI), None, deactivation_date=date(2020, 1, 1))
    answer = _dated(npi_person(NPI), date(2026, 1, 3), deactivation_date=date(2021, 1, 1))

    assert carry_forward(answer, mirrored).deactivation_date == date(2021, 1, 1)
    assert carry_forward(answer, None) is answer


def test_the_roster_npi_with_its_last_name_is_exact_and_reports_the_rest() -> None:
    provider = individual(
        npi=NPI, middle="Quinn", licenses=(License(number="SAMPLE-1", state="NY"),)
    )
    record = npi_person(NPI, middle="QUINN", licenses=(("SAMPLE-1", "NY"),))

    match = match_provider_to_npi(provider, record)

    assert match.level is MatchLevel.EXACT
    fields = {item.field: item for item in match.evidence}
    assert fields[MatchField.NPI].detail == "on the roster"
    assert fields[MatchField.LAST_NAME].matched
    assert fields[MatchField.FIRST_NAME].matched
    assert fields[MatchField.MIDDLE_NAME].matched
    assert fields[MatchField.LICENSE].matched


def test_supporting_evidence_never_lowers_the_match() -> None:
    provider = individual(
        npi=NPI, first="Jordan", licenses=(License(number="OTHER-9", state="NY"),)
    )
    record = npi_person(NPI, licenses=(("SAMPLE-1", "NY"),))

    match = match_provider_to_npi(provider, record)

    assert match.level is MatchLevel.EXACT
    fields = {item.field: item for item in match.evidence}
    assert not fields[MatchField.FIRST_NAME].matched
    assert not fields[MatchField.LICENSE].matched


def test_a_last_name_that_differs_is_high() -> None:
    record = npi_person(NPI, last="OTHERNAME")

    match = match_provider_to_npi(individual(npi=NPI), record)

    assert match.level is MatchLevel.HIGH
    assert "name differs from the registry" in match.explanation


def test_the_other_kind_or_no_name_under_the_npi_is_high() -> None:
    other_kind = match_provider_to_npi(individual(npi=NPI), npi_organization(NPI))
    no_name = match_provider_to_npi(individual(npi=NPI), npi_stub(NPI))

    assert other_kind.level is MatchLevel.HIGH
    assert "it lists an organization under this NPI" in other_kind.explanation
    assert no_name.level is MatchLevel.HIGH
    assert "publishes no name" in no_name.explanation


def test_an_organization_matches_without_designators_or_by_other_name() -> None:
    by_name = match_provider_to_npi(
        organization("Sampleville Care Group", npi=NPI), npi_organization(NPI)
    )
    by_other = match_provider_to_npi(
        organization("Sampleville Clinic", npi=NPI),
        npi_organization(NPI, other_names=("SAMPLEVILLE CLINIC",)),
    )
    differs = match_provider_to_npi(
        organization("Elsewhere Health", npi=NPI), npi_organization(NPI)
    )

    assert by_name.level is MatchLevel.EXACT
    assert "legal suffix ignored" in by_name.explanation
    assert by_other.level is MatchLevel.EXACT
    assert differs.level is MatchLevel.HIGH


def test_another_npi_is_no_match() -> None:
    assert match_provider_to_npi(individual(npi=OTHER), npi_person(NPI)).level is MatchLevel.NONE


def test_a_query_is_stripped_and_needs_a_last_or_organization_name() -> None:
    query = NpiQuery(last_name="  Testperson ", first_name=" ", state="NY", city="")

    assert (query.last_name, query.first_name, query.city) == ("Testperson", None, None)
    assert NpiQuery(organization_name="Sampleville Care").organization_name == "Sampleville Care"
    with pytest.raises(ValueError, match="last name or an organization name"):
        NpiQuery(first_name="Avery", state="NY")


@pytest.mark.parametrize("name", ["Te*", "Testp*"])
def test_a_name_may_end_with_a_wildcard(name: str) -> None:
    assert NpiQuery(last_name=name).last_name == name


@pytest.mark.parametrize(
    "fields",
    [
        {"last_name": "T*"},
        {"last_name": "*erson"},
        {"last_name": "Te*st"},
        {"last_name": "Te**"},
        {"last_name": "Testperson", "city": "Sample*"},
        {"last_name": "Testperson", "state": "ny"},
        {"last_name": "Testperson", "state": "NYC"},
        {"last_name": "Testperson", "entity_kind": EntityKind.UNKNOWN},
    ],
)
def test_a_query_the_registry_would_refuse_is_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"wildcard|state|entity_kind"):
        NpiQuery(**fields)  # type: ignore[arg-type]  # the parametrized fields are typed loosely


def _snapshot(outcome: Outcome) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="nppes_api",
        fetched_at=NOW,
        outcome=outcome,
        source_url="https://example.test/registry/api/",
    )


def test_an_answer_found_is_none_unless_the_registry_answered() -> None:
    completed = _snapshot(Outcome.COMPLETED)

    assert NpiAnswer(snapshot=completed, records=(), result_count=0).found is False
    assert NpiAnswer(snapshot=completed, records=(npi_person(NPI),), result_count=1).found is True
    assert NpiAnswer(snapshot=_snapshot(Outcome.BLOCKED)).found is None
    with pytest.raises(ValueError, match="exactly with a completed answer"):
        NpiAnswer(snapshot=_snapshot(Outcome.FAILED), records=(), result_count=0)
    with pytest.raises(ValueError, match="exactly with a completed answer"):
        NpiAnswer(snapshot=completed)


def test_a_taxonomy_licence_in_another_state_is_reported_not_matched() -> None:
    provider = individual(npi=NPI, licenses=(License(number="SAMPLE-1", state="NJ"),))
    record = npi_person(NPI).model_copy(
        update={
            "taxonomies": (
                NpiTaxonomy(
                    code="207Q00000X", primary=True, license_number="X", license_state="NY"
                ),
            ),
            "person_name": PersonName(last="TESTPERSON", first="AVERY"),
        }
    )

    match = match_provider_to_npi(provider, record)

    licence = next(item for item in match.evidence if item.field is MatchField.LICENSE)
    assert not licence.matched
    assert licence.detail == "no licence in NJ in the registry"
    assert match.level is MatchLevel.EXACT
    assert record.status is NpiStatus.ACTIVE
