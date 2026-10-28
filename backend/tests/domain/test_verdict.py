from datetime import datetime, timedelta
from uuid import UUID, uuid4

from credentialing.domain.enums import (
    CredentialType,
    Finding,
    Freshness,
    Outcome,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import Provenance, VerificationResult
from credentialing.domain.policies import derive_verdict
from tests.synthetic import NOW

PROVIDER = uuid4()
ORGANIZATION = uuid4()


def _result(
    source_id: str,
    finding: Finding | None = Finding.NO_RECORD,
    *,
    outcome: Outcome = Outcome.COMPLETED,
    at: datetime = NOW,
    list_updated: datetime | None = NOW - timedelta(days=6),
    id: UUID | None = None,
    record_status: str | None = None,
) -> VerificationResult:
    return VerificationResult(
        id=id or uuid4(),
        organization_id=ORGANIZATION,
        provider_id=PROVIDER,
        credential_type=CredentialType.EXCLUSION,
        source_id=source_id,
        outcome=outcome,
        finding=finding if outcome is Outcome.COMPLETED else None,
        summary="synthetic result",
        details={"record_status": record_status} if record_status else {},
        provenance=Provenance(
            source_id=source_id,
            source_url=f"https://example.test/{source_id}",
            method=VerificationMethod.BULK_DATASET,
            verified_at=at,
            source_updated_at=list_updated,
        ),
        freshness=Freshness.CACHED,
        created_at=at,
    )


def _failed(source_id: str, outcome: Outcome = Outcome.SOURCE_UNAVAILABLE) -> VerificationResult:
    return _result(source_id, None, outcome=outcome, list_updated=None)


def test_without_results_the_provider_is_not_checked() -> None:
    verdict = derive_verdict([])

    assert verdict.verdict is Verdict.NOT_CHECKED
    assert verdict.sources_checked == 0
    assert verdict.results == ()
    assert verdict.oldest_list_updated_at is None
    assert verdict.last_checked_at is None


def test_a_found_record_comes_before_everything_else() -> None:
    verdict = derive_verdict(
        [
            _result("list_a", Finding.AMBIGUOUS),
            _result("list_b", Finding.RECORD_FOUND),
            _failed("list_c"),
        ]
    )

    assert verdict.verdict is Verdict.LISTED
    assert verdict.listed_in == ("list_b",)
    assert verdict.review_in == ("list_a",)
    assert verdict.unchecked == ("list_c",)
    assert verdict.sources_checked == 3


def test_review_comes_before_checks_that_could_not_run() -> None:
    verdict = derive_verdict([_result("list_a", Finding.AMBIGUOUS), _failed("list_b")])

    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.review_in == ("list_a",)
    assert verdict.listed_in == ()


def test_a_check_that_could_not_run_is_never_clean() -> None:
    verdict = derive_verdict(
        [_result("list_a"), _failed("list_b"), _failed("list_c", Outcome.BLOCKED)]
    )

    assert verdict.verdict is Verdict.COULD_NOT_CHECK
    assert verdict.unchecked == ("list_b", "list_c")


def test_every_outcome_other_than_completed_counts_as_could_not_check() -> None:
    for outcome in set(Outcome) - {Outcome.COMPLETED}:
        verdict = derive_verdict([_failed("list_a", outcome)])

        assert verdict.verdict is Verdict.COULD_NOT_CHECK, outcome
        assert verdict.unchecked == ("list_a",)


def test_no_records_only_when_every_source_answered_with_none() -> None:
    verdict = derive_verdict([_result("list_a"), _result("list_b")])

    assert verdict.verdict is Verdict.NO_RECORDS
    assert verdict.sources_checked == 2
    assert verdict.listed_in == verdict.review_in == verdict.unchecked == ()


def test_the_figures_come_from_completed_results_and_the_latest_check() -> None:
    oldest = NOW - timedelta(days=20)
    verdict = derive_verdict(
        [
            _result("list_b", at=NOW - timedelta(hours=1), list_updated=NOW - timedelta(days=3)),
            _result("list_a", at=NOW, list_updated=oldest),
            _result("list_c", at=NOW - timedelta(hours=2), list_updated=None),
            # A failed check's list date is not a list the answer relied on.
            _result(
                "list_d",
                None,
                outcome=Outcome.FAILED,
                at=NOW - timedelta(hours=3),
                list_updated=oldest - timedelta(days=100),
            ),
        ]
    )

    assert verdict.oldest_list_updated_at == oldest
    assert verdict.last_checked_at == NOW
    assert [r.source_id for r in verdict.results] == ["list_a", "list_b", "list_c", "list_d"]


def test_a_source_given_twice_counts_only_its_newest_result() -> None:
    old = _result("list_a", Finding.RECORD_FOUND, at=NOW - timedelta(days=1))
    new = _result("list_a", at=NOW)
    same_moment = [
        _result("list_b", Finding.AMBIGUOUS, id=UUID(int=1)),
        _result("list_b", id=UUID(int=2)),
    ]

    verdict = derive_verdict([new, old, *same_moment])

    assert verdict.verdict is Verdict.NO_RECORDS
    assert verdict.results == (new, same_moment[1])
    assert verdict.sources_checked == 2


def test_a_found_record_that_has_ended_needs_review_not_listed() -> None:
    verdict = derive_verdict(
        [_result("list_a", Finding.RECORD_FOUND, record_status="ended"), _result("list_b")]
    )

    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.listed_in == ()
    assert verdict.review_in == ("list_a",)


def test_one_standing_listing_is_listed_even_beside_an_ended_one() -> None:
    verdict = derive_verdict(
        [
            _result("list_a", Finding.RECORD_FOUND, record_status="ended"),
            _result("list_b", Finding.RECORD_FOUND, record_status="active"),
        ]
    )

    assert verdict.verdict is Verdict.LISTED
    assert verdict.listed_in == ("list_b",)
    assert verdict.review_in == ("list_a",)


def test_an_ended_listing_still_outranks_a_check_that_could_not_run() -> None:
    verdict = derive_verdict(
        [_result("list_a", Finding.RECORD_FOUND, record_status="ended"), _failed("list_b")]
    )

    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.unchecked == ("list_b",)


def test_a_found_record_without_a_status_counts_as_standing() -> None:
    verdict = derive_verdict([_result("list_a", Finding.RECORD_FOUND)])

    assert verdict.verdict is Verdict.LISTED
    assert verdict.listed_in == ("list_a",)


def test_a_standing_related_party_record_is_flagged() -> None:
    verdict = derive_verdict(
        [
            _result("list_a", Finding.AMBIGUOUS),
            _result("list_b", Finding.RELATED_PARTY, record_status="active"),
            _failed("list_c"),
        ]
    )

    assert verdict.verdict is Verdict.FLAGGED
    assert verdict.flagged_in == ("list_b",)
    assert verdict.listed_in == ()
    assert verdict.review_in == ("list_a",)
    assert verdict.unchecked == ("list_c",)


def test_listed_comes_before_flagged() -> None:
    verdict = derive_verdict(
        [_result("list_a", Finding.RELATED_PARTY), _result("list_b", Finding.RECORD_FOUND)]
    )

    assert verdict.verdict is Verdict.LISTED
    assert verdict.listed_in == ("list_b",)
    assert verdict.flagged_in == ("list_a",)


def test_an_ended_related_party_record_needs_review_not_flagged() -> None:
    verdict = derive_verdict(
        [_result("list_a", Finding.RELATED_PARTY, record_status="ended"), _result("list_b")]
    )

    assert verdict.verdict is Verdict.NEEDS_REVIEW
    assert verdict.flagged_in == ()
    assert verdict.review_in == ("list_a",)
