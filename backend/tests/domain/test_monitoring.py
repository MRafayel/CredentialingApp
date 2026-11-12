"""The monitored provider's status, what changed between two results, and when a recheck is due."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.domain.enums import (
    Attention,
    CredentialType,
    Finding,
    Freshness,
    IdentityState,
    Outcome,
    RecordStatus,
    RegistryStatus,
    RosterRole,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import Provenance, Provider, ResultChange, VerificationResult
from credentialing.domain.policies import (
    HISTORY_RETENTION,
    MONITORING_DUE_TOLERANCE,
    MONITORING_INTERVAL,
    attention_rank,
    derive_status,
    history_cutoff,
    identity_state,
    recheck_due,
    result_change,
)
from tests.synthetic import NOW, individual, make_npi

NPI = make_npi("160000001")
FEDERAL, STATE = "fake_federal", "fake_state"


def _result(
    source_id: str = FEDERAL,
    finding: Finding | None = Finding.NO_RECORD,
    *,
    outcome: Outcome = Outcome.COMPLETED,
    at: datetime = NOW,
    details: dict[str, str] | None = None,
    credential_type: CredentialType = CredentialType.EXCLUSION,
    changed_from: ResultChange | None = None,
) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        organization_id=uuid4(),
        provider_id=uuid4(),
        credential_type=credential_type,
        source_id=source_id,
        outcome=outcome,
        finding=finding if outcome is Outcome.COMPLETED else None,
        summary="synthetic result",
        details=details or {},
        provenance=Provenance(
            source_id=source_id,
            source_url=f"https://example.test/{source_id}",
            method=VerificationMethod.BULK_DATASET,
            verified_at=at,
        ),
        freshness=Freshness.CACHED,
        created_at=at,
        changed_from=changed_from,
    )


def _npi(
    status: RegistryStatus | None = RegistryStatus.ACTIVE,
    *,
    outcome: Outcome = Outcome.COMPLETED,
    at: datetime = NOW,
    reason: str | None = None,
    changed_from: ResultChange | None = None,
) -> VerificationResult:
    details = {"npi_status": status.value} if status else {}
    if reason:
        details["reason"] = reason
    finding = Finding.RECORD_FOUND if outcome is Outcome.COMPLETED else None
    return _result(
        "nppes_monthly",
        finding,
        outcome=outcome,
        at=at,
        details=details,
        credential_type=CredentialType.NPI,
        changed_from=changed_from,
    )


def _change(at: datetime = NOW - timedelta(days=15)) -> ResultChange:
    return ResultChange(outcome=Outcome.COMPLETED, finding=Finding.NO_RECORD, verified_at=at)


def _provider(
    *, npi: str | None = NPI, role: RosterRole = RosterRole.PRACTITIONER, viewed: datetime | None
) -> Provider:
    return individual(npi=npi).model_copy(update={"role": role, "last_viewed_at": viewed})


# --- result_change -------------------------------------------------------------------------


def test_a_first_result_has_no_change() -> None:
    assert result_change(None, _result()) is None


def test_the_same_answer_again_is_no_change() -> None:
    before = _result(at=NOW - timedelta(days=15))

    assert result_change(before, _result()) is None


def test_a_new_finding_keeps_the_previous_answer() -> None:
    before = _result(at=NOW - timedelta(days=15))

    change = result_change(before, _result(finding=Finding.RECORD_FOUND))

    assert change == ResultChange(
        outcome=Outcome.COMPLETED,
        finding=Finding.NO_RECORD,
        verified_at=NOW - timedelta(days=15),
    )


def test_a_record_that_ended_is_a_change_of_record_status() -> None:
    before = _result(finding=Finding.RECORD_FOUND, details={"record_status": "active"})
    after = _result(finding=Finding.RECORD_FOUND, details={"record_status": "ended"})

    change = result_change(before, after)

    assert change is not None
    assert change.record_status is RecordStatus.ACTIVE
    assert change.finding is Finding.RECORD_FOUND


@pytest.mark.parametrize(
    "outcome",
    [Outcome.SOURCE_UNAVAILABLE, Outcome.BLOCKED, Outcome.FAILED, Outcome.MANUAL_REQUIRED],
)
def test_a_could_not_check_result_never_carries_a_change(outcome: Outcome) -> None:
    before = _result(finding=Finding.RECORD_FOUND, details={"record_status": "active"})

    assert result_change(before, _result(outcome=outcome)) is None
    assert result_change(_npi(), _npi(None, outcome=outcome)) is None


def test_a_first_answer_after_could_not_check_results_is_no_change() -> None:
    """A list's first load (or the end of an outage) is not a change: only answers compare."""
    unavailable = _result(outcome=Outcome.SOURCE_UNAVAILABLE, at=NOW - timedelta(days=1))

    assert result_change(unavailable, _result()) is None


def test_an_answer_is_compared_with_the_previous_answer_across_a_gap() -> None:
    """No record, then unavailable, then a record found: the caller hands the last completed
    result (no record), so the change is caught and described by it."""
    answered = _result(at=NOW - timedelta(days=30))
    gap = _result(outcome=Outcome.SOURCE_UNAVAILABLE, at=NOW - timedelta(days=15))
    found = _result(finding=Finding.RECORD_FOUND, details={"record_status": "active"})

    assert result_change(answered, gap) is None
    change = result_change(answered, found)

    assert change == ResultChange(
        outcome=Outcome.COMPLETED,
        finding=Finding.NO_RECORD,
        verified_at=NOW - timedelta(days=30),
    )


def test_an_npi_that_was_deactivated_is_a_change_of_registry_status() -> None:
    change = result_change(_npi(RegistryStatus.ACTIVE), _npi(RegistryStatus.DEACTIVATED))

    assert change is not None
    assert change.registry_status is RegistryStatus.ACTIVE
    assert result_change(_npi(), _npi()) is None


def test_other_details_are_not_compared() -> None:
    before = _result(details={"list_fetched_at": "2026-01-01T00:00:00+00:00"})

    assert result_change(before, _result(details={"candidates_compared": "3"})) is None


# --- identity ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "npi", "latest", "expected"),
    [
        (RosterRole.PRACTITIONER, None, None, IdentityState.NPI_MISSING),
        (RosterRole.WORKFORCE, None, None, IdentityState.NOT_EXPECTED),
        (RosterRole.VENDOR, None, None, IdentityState.NOT_EXPECTED),
        (RosterRole.WORKFORCE, NPI, None, IdentityState.NPI_UNVERIFIED),
        (RosterRole.PRACTITIONER, NPI, None, IdentityState.NPI_UNVERIFIED),
        (RosterRole.VENDOR, NPI, _npi(RegistryStatus.ACTIVE), IdentityState.NPI_ACTIVE),
        (RosterRole.PRACTITIONER, NPI, _npi(RegistryStatus.ACTIVE), IdentityState.NPI_ACTIVE),
        (
            RosterRole.PRACTITIONER,
            NPI,
            _npi(RegistryStatus.DEACTIVATED),
            IdentityState.NPI_DEACTIVATED,
        ),
        (
            RosterRole.PRACTITIONER,
            NPI,
            _npi(RegistryStatus.MIRROR_ONLY_ACTIVE),
            IdentityState.NPI_UNCERTAIN,
        ),
        (RosterRole.PRACTITIONER, NPI, _npi(RegistryStatus.NOT_FOUND), IdentityState.NPI_UNCERTAIN),
        (
            RosterRole.PRACTITIONER,
            NPI,
            _npi(None, outcome=Outcome.SOURCE_UNAVAILABLE),
            IdentityState.NPI_UNCERTAIN,
        ),
        (
            RosterRole.PRACTITIONER,
            NPI,
            _npi(None, outcome=Outcome.MANUAL_REQUIRED, reason="no_npi"),
            IdentityState.NPI_UNVERIFIED,
        ),
    ],
)
def test_identity_follows_the_role_the_npi_and_the_latest_npi_result(
    role: RosterRole,
    npi: str | None,
    latest: VerificationResult | None,
    expected: IdentityState,
) -> None:
    assert identity_state(_provider(npi=npi, role=role, viewed=None), latest) is expected


# --- derive_status -------------------------------------------------------------------------


def test_a_provider_never_checked_needs_its_first_check() -> None:
    status = derive_status(_provider(viewed=None), [], None)

    assert status.verdict is Verdict.NOT_CHECKED
    assert status.identity is IdentityState.NPI_UNVERIFIED
    assert status.attention is Attention.NOT_CHECKED
    assert (status.last_checked_at, status.next_check_at) == (None, None)
    assert not status.changed


def test_a_practitioner_without_an_npi_is_missing_it_before_the_first_check() -> None:
    status = derive_status(_provider(npi=None, viewed=None), [], None)

    assert status.attention is Attention.NPI_MISSING


def test_a_workforce_member_without_an_npi_needs_nobody_once_checked() -> None:
    provider = _provider(npi=None, role=RosterRole.WORKFORCE, viewed=None)

    status = derive_status(provider, [_result()], None)

    assert status.identity is IdentityState.NOT_EXPECTED
    assert status.verdict is Verdict.NO_RECORDS
    assert status.attention is None


@pytest.mark.parametrize(
    ("exclusions", "npi_status", "expected"),
    [
        ([_result(finding=Finding.RECORD_FOUND)], RegistryStatus.DEACTIVATED, Attention.LISTED),
        ([_result(finding=Finding.RELATED_PARTY)], RegistryStatus.ACTIVE, Attention.FLAGGED),
        ([_result(finding=Finding.AMBIGUOUS)], RegistryStatus.ACTIVE, Attention.NEEDS_REVIEW),
        (
            [_result(outcome=Outcome.BLOCKED)],
            RegistryStatus.DEACTIVATED,
            Attention.COULD_NOT_CHECK,
        ),
        ([_result()], RegistryStatus.DEACTIVATED, Attention.NPI_DEACTIVATED),
        ([_result()], RegistryStatus.NOT_FOUND, Attention.NPI_UNCERTAIN),
        ([_result()], RegistryStatus.ACTIVE, None),
    ],
)
def test_attention_is_the_most_serious_reason(
    exclusions: list[VerificationResult],
    npi_status: RegistryStatus,
    expected: Attention | None,
) -> None:
    status = derive_status(_provider(viewed=None), exclusions, _npi(npi_status))

    assert status.attention is expected


def test_the_verdicts_sources_are_carried() -> None:
    status = derive_status(
        _provider(viewed=None),
        [_result(FEDERAL, Finding.RECORD_FOUND), _result(STATE, outcome=Outcome.BLOCKED)],
        _npi(),
    )

    assert status.verdict is Verdict.LISTED
    assert (status.listed_in, status.unchecked) == ((FEDERAL,), (STATE,))


def test_a_change_needs_attention_until_the_provider_is_opened() -> None:
    changed = _result(finding=Finding.AMBIGUOUS, changed_from=_change())
    cleared = _result(changed_from=_change())

    unseen = derive_status(_provider(viewed=None), [cleared], _npi())
    seen_before = derive_status(_provider(viewed=NOW - timedelta(hours=1)), [cleared], _npi())
    seen_after = derive_status(_provider(viewed=NOW + timedelta(hours=1)), [cleared], _npi())
    serious = derive_status(_provider(viewed=None), [changed], _npi())

    assert (unseen.changed, unseen.attention) == (True, Attention.CHANGED)
    assert (seen_before.changed, seen_before.attention) == (True, Attention.CHANGED)
    assert (seen_after.changed, seen_after.attention) == (False, None)
    assert (serious.changed, serious.attention) == (True, Attention.NEEDS_REVIEW)


def test_a_change_of_the_npi_counts_too() -> None:
    status = derive_status(_provider(viewed=None), [_result()], _npi(changed_from=_change()))

    assert status.attention is Attention.CHANGED


def test_a_change_nobody_saw_survives_a_later_recheck_that_found_nothing_new() -> None:
    earlier_change = NOW - timedelta(days=15)
    provider = _provider(viewed=NOW - timedelta(days=20))

    unseen = derive_status(provider, [_result()], _npi(), last_change_at=earlier_change)
    seen = derive_status(
        provider.model_copy(update={"last_viewed_at": NOW - timedelta(days=1)}),
        [_result()],
        _npi(),
        last_change_at=earlier_change,
    )

    assert unseen.changed
    assert not seen.changed
    # When the newest change happened is kept, seen or not.
    assert unseen.changed_at == seen.changed_at == earlier_change


def test_changed_at_is_the_newest_change_and_none_without_one() -> None:
    older = _result(at=NOW - timedelta(days=3), changed_from=_change())
    newer = _npi(at=NOW - timedelta(days=1), changed_from=_change())
    provider = _provider(viewed=None)

    assert derive_status(provider, [older], newer).changed_at == NOW - timedelta(days=1)
    assert derive_status(provider, [_result()], _npi()).changed_at is None


def test_the_next_check_is_due_an_interval_after_the_oldest_result_it_renews() -> None:
    old, new = NOW - timedelta(days=10), NOW
    exclusions = [_result(FEDERAL, at=old), _result(STATE, at=new)]

    status = derive_status(_provider(viewed=None), exclusions, _npi(at=new))

    assert status.last_checked_at == new
    assert status.next_check_at == old + MONITORING_INTERVAL


def test_a_recheck_of_one_list_never_postpones_the_full_recheck() -> None:
    full = NOW - timedelta(days=14)
    exclusions = [_result(FEDERAL, at=full), _result(STATE, at=NOW)]

    status = derive_status(_provider(viewed=None), exclusions, _npi(at=full))

    assert status.next_check_at == full + MONITORING_INTERVAL


def test_an_old_npi_result_makes_the_recheck_due() -> None:
    status = derive_status(
        _provider(viewed=None), [_result(at=NOW)], _npi(at=NOW - timedelta(days=20))
    )

    assert status.next_check_at == NOW - timedelta(days=20) + MONITORING_INTERVAL


def test_without_an_npi_result_a_provider_with_an_npi_is_due() -> None:
    assert derive_status(_provider(viewed=None), [_result()], None).next_check_at is None


def test_without_an_npi_only_the_lists_count() -> None:
    status = derive_status(_provider(npi=None, viewed=None), [_result(at=NOW)], None)

    assert status.next_check_at == NOW + MONITORING_INTERVAL


def test_a_list_connected_since_the_last_check_makes_the_recheck_due() -> None:
    status = derive_status(
        _provider(viewed=None), [_result(FEDERAL)], _npi(), lists={FEDERAL, STATE}
    )

    assert status.next_check_at is None


def test_a_disconnected_lists_old_answer_does_not_keep_the_provider_due() -> None:
    exclusions = [_result(FEDERAL, at=NOW), _result("retired_list", at=NOW - timedelta(days=90))]

    status = derive_status(_provider(viewed=None), exclusions, _npi(), lists={FEDERAL})

    assert status.next_check_at == NOW + MONITORING_INTERVAL
    assert status.verdict is Verdict.NO_RECORDS


# --- recheck_due and ranking ---------------------------------------------------------------


def test_a_recheck_is_due_once_its_time_comes_within_the_tolerance() -> None:
    assert recheck_due(None, NOW)
    assert recheck_due(NOW, NOW)
    assert recheck_due(NOW + MONITORING_DUE_TOLERANCE, NOW)
    assert not recheck_due(NOW + MONITORING_DUE_TOLERANCE + timedelta(seconds=1), NOW)


def test_attention_ranks_follow_the_declared_order_and_none_comes_last() -> None:
    ranks = [attention_rank(attention) for attention in Attention]

    assert ranks == sorted(ranks)
    assert ranks[0] == attention_rank(Attention.LISTED) == 0
    assert attention_rank(None) > ranks[-1]


def test_history_is_kept_six_months() -> None:
    assert timedelta(days=183) == HISTORY_RETENTION
    assert history_cutoff(NOW) == NOW - timedelta(days=183)
