from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    Finding,
    Freshness,
    MatchLevel,
    Outcome,
)


def test_failure_outcomes_are_distinct_from_completed() -> None:
    could_not_check = set(Outcome) - {Outcome.COMPLETED}

    assert could_not_check == {
        Outcome.SOURCE_UNAVAILABLE,
        Outcome.BLOCKED,
        Outcome.MANUAL_REQUIRED,
        Outcome.FAILED,
    }
    assert Finding.NO_RECORD.value not in {outcome.value for outcome in Outcome}


def test_vocabularies_are_lower_case_strings() -> None:
    for vocabulary in (CredentialType, MatchLevel, Freshness, AccessPolicy, ChangeSignal):
        for member in vocabulary:
            assert member == member.value == member.name.lower()
