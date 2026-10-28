from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    ExclusionAction,
    Finding,
    Freshness,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.errors import InvalidProviderError
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    FetchResult,
    License,
    PersonName,
    Provenance,
    Provider,
    SourceSnapshot,
    VerificationResult,
    compute_content_hash,
)
from tests.synthetic import NOW, exclusion, individual, make_npi


def _provenance() -> Provenance:
    return Provenance(
        source_id="test_exclusions",
        source_url="https://example.test/list",
        method=VerificationMethod.BULK_DATASET,
        verified_at=NOW,
    )


def _result(outcome: Outcome, finding: Finding | None) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        organization_id=uuid4(),
        provider_id=uuid4(),
        credential_type=CredentialType.EXCLUSION,
        source_id="test_exclusions",
        outcome=outcome,
        finding=finding,
        summary="synthetic",
        provenance=_provenance(),
        freshness=Freshness.LIVE,
        created_at=NOW,
    )


def test_models_are_immutable() -> None:
    provider = individual()

    with pytest.raises(ValidationError):
        provider.npi = make_npi("100000001")  # type: ignore[misc]  # proving frozen at runtime


def test_individual_provider_needs_a_person_name() -> None:
    with pytest.raises(ValidationError) as caught:
        Provider(
            id=uuid4(),
            organization_id=uuid4(),
            entity_kind=EntityKind.INDIVIDUAL,
            organization_name="Sampleville Care Group",
            created_at=NOW,
        )

    assert isinstance(caught.value.errors()[0]["ctx"]["error"], InvalidProviderError)


def test_organization_provider_needs_an_organization_name() -> None:
    with pytest.raises(ValidationError, match="organization name"):
        Provider(
            id=uuid4(),
            organization_id=uuid4(),
            entity_kind=EntityKind.ORGANIZATION,
            person_name=PersonName(last="Testperson", first="Avery"),
            created_at=NOW,
        )


def test_timestamps_must_be_timezone_aware() -> None:
    naive = datetime(2026, 1, 15, 12, 0)  # noqa: DTZ001 - the naive value under test

    with pytest.raises(ValidationError):
        Provider(
            id=uuid4(),
            organization_id=uuid4(),
            entity_kind=EntityKind.INDIVIDUAL,
            person_name=PersonName(last="Testperson", first="Avery"),
            created_at=naive,
        )


def test_completed_result_needs_a_finding() -> None:
    assert _result(Outcome.COMPLETED, Finding.NO_RECORD).finding is Finding.NO_RECORD
    with pytest.raises(ValidationError, match="needs a finding"):
        _result(Outcome.COMPLETED, None)


@pytest.mark.parametrize(
    "outcome",
    [Outcome.SOURCE_UNAVAILABLE, Outcome.BLOCKED, Outcome.MANUAL_REQUIRED, Outcome.FAILED],
)
def test_a_check_that_could_not_run_has_no_finding(outcome: Outcome) -> None:
    assert _result(outcome, None).finding is None
    with pytest.raises(ValidationError, match="cannot have a finding"):
        _result(outcome, Finding.NO_RECORD)


def test_records_only_accompany_a_completed_snapshot() -> None:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=NOW,
        outcome=Outcome.BLOCKED,
        source_url="https://example.test/list",
    )

    assert FetchResult(snapshot=snapshot).records is None
    with pytest.raises(ValidationError, match="completed snapshot"):
        FetchResult(snapshot=snapshot, records=(exclusion(),))


def test_record_hash_ignores_raw_cells_and_cosmetic_name_changes() -> None:
    first = exclusion(last="TESTPERSON", first="Avery", raw={"LASTNAME": "TESTPERSON"})
    second = exclusion(
        last="Testperson MD",
        first=" avery ",
        raw={"LASTNAME": "Testperson MD", "NEW COLUMN": "x"},
        valid_from=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert first.id != second.id
    assert first.record_hash == second.record_hash
    assert len(first.record_hash) == 64


@pytest.mark.parametrize(
    "changed",
    [
        {"source_id": "other_exclusions"},
        {"entity_kind": EntityKind.ORGANIZATION},
        {"last": "Samplename"},
        {"first": "Blake"},
        {"middle": "Jordan"},
        {"organization_name": "Sampleville Care Group"},
        {"npi": "1234567893"},
        {"license": License(number="000001", state="NY")},
        {"effective_date": date(2024, 3, 2)},
        {"action": ExclusionAction.SUSPENSION},
    ],
)
def test_record_hash_changes_with_each_identifying_field(changed: dict[str, object]) -> None:
    base = exclusion()
    other = exclusion(**changed)  # type: ignore[arg-type]  # one field of mixed types per case

    assert base.record_hash != other.record_hash


def test_an_end_date_appearing_changes_content_but_not_identity() -> None:
    listed = exclusion(raw={"LASTNAME": "TESTPERSON"})
    reinstated = exclusion(end_date=date(2026, 1, 1), raw={"LASTNAME": "TESTPERSON"})

    assert reinstated.record_hash == listed.record_hash
    assert reinstated.content_hash != listed.content_hash


def test_content_hash_ignores_raw_key_order_and_alias_order() -> None:
    def content(raw: dict[str, str], aliases: tuple[str, ...]) -> str:
        return compute_content_hash(
            end_date=None,
            reason=" synthetic reason ",
            action_raw=None,
            aliases=aliases,
            date_of_birth=None,
            address=Address(city="Sampleville"),
            state_provider_id=None,
            license=None,
            raw=raw,
        )

    assert content({"A": "1", "B": "2"}, ("Avery T", "A Testperson")) == content(
        {"B": "2", "A": "1"}, ("A Testperson", "Avery T")
    )
    assert content({"A": "1", "B": "2"}, ()) != content({"A": "1", "B": "3"}, ())


@pytest.mark.parametrize(
    "changed",
    [
        {"end_date": date(2026, 1, 1)},
        {"aliases": ("Avery Marriedname",)},
        {"date_of_birth": date(1970, 5, 17)},
        {"raw": {"LASTNAME": "Testperson", "FIRSTNAME": "Avery", "NOTE": "x"}},
        {"license": License(number="0012345", state="NY", kind="Physician")},
        {"related_npis": (make_npi("100000009"),)},
        {"related_parties": ("Sample Family Care",)},
    ],
)
def test_content_hash_changes_with_each_content_field(changed: dict[str, object]) -> None:
    base = exclusion(license=License(number="0012345", state="NY"))
    other = exclusion(**{"license": base.license, **changed})  # type: ignore[arg-type]  # mixed

    assert base.content_hash != other.content_hash


def test_create_and_direct_construction_agree() -> None:
    record = exclusion()
    rebuilt = ExclusionRecord(**record.model_dump())

    assert rebuilt == record


def test_content_hash_without_related_fields_is_unchanged() -> None:
    def content(related_npis: tuple[str, ...], related_parties: tuple[str, ...]) -> str:
        return compute_content_hash(
            end_date=None,
            reason=None,
            action_raw="Termination",
            aliases=(),
            date_of_birth=None,
            address=None,
            state_provider_id=None,
            license=None,
            raw={"NAME": "Testperson, Avery"},
            related_npis=related_npis,
            related_parties=related_parties,
        )

    # The digest of this row as stored before related NPIs and parties existed.
    stored = "058326724744ad1f45eddd85fcd0c40327f9044429bddff553ddf2f26e37aa7f"
    assert content((), ()) == stored
    assert content((), (" ",)) == stored


def test_create_keeps_related_npis_other_than_its_own() -> None:
    own, other = make_npi("100000001"), make_npi("100000002")

    record = exclusion(npi=own, related_npis=(own, other, other))

    assert record.npi == own
    assert record.related_npis == (other,)


def test_a_record_cannot_list_its_own_npi_as_related() -> None:
    record = exclusion(npi=make_npi("100000001"))

    with pytest.raises(ValidationError):
        ExclusionRecord(**{**record.model_dump(), "related_npis": (record.npi,)})
