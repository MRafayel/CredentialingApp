from datetime import date
from uuid import uuid4

import pytest

from credentialing.domain.enums import EntityKind, MatchField, MatchLevel
from credentialing.domain.models import License, MatchEvidence, PersonName, Provider
from credentialing.domain.policies import candidate_keys, match_provider_to_record
from tests.synthetic import NOW, exclusion, individual, make_npi, organization

NPI = make_npi("100000001")
OTHER_NPI = make_npi("100000002")
BIRTH = date(1970, 5, 17)


def test_equal_npis_are_an_exact_match() -> None:
    result = match_provider_to_record(
        individual(npi=NPI), exclusion(last="Othername", first="Blake", npi=NPI)
    )

    assert result.level is MatchLevel.EXACT
    assert result.evidence == (MatchEvidence(field=MatchField.NPI, matched=True),)
    assert result.explanation == "Exact match: NPI matches."


def test_different_npis_rule_out_a_same_name_record() -> None:
    result = match_provider_to_record(individual(npi=NPI), exclusion(npi=OTHER_NPI))

    assert result.level is MatchLevel.NONE
    assert result.evidence == (MatchEvidence(field=MatchField.NPI, matched=False),)


def test_name_only_is_potential_never_exact() -> None:
    result = match_provider_to_record(individual(npi=NPI), exclusion(npi=None))

    assert result.level is MatchLevel.POTENTIAL
    assert [item.field for item in result.evidence] == [
        MatchField.LAST_NAME,
        MatchField.FIRST_NAME,
    ]
    assert result.explanation == "Potential match: last name matches, first name matches."


def test_name_and_date_of_birth_are_high() -> None:
    result = match_provider_to_record(
        individual(date_of_birth=BIRTH), exclusion(date_of_birth=BIRTH)
    )

    assert result.level is MatchLevel.HIGH
    assert result.evidence[-1] == MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=True)


def test_name_and_license_are_high() -> None:
    own = License(number="0012 345", state="NY", kind="Physician")
    listed = License(number="0012345", state="NY")

    result = match_provider_to_record(individual(licenses=(own,)), exclusion(license=listed))

    assert result.level is MatchLevel.HIGH
    assert result.evidence[-1] == MatchEvidence(field=MatchField.LICENSE, matched=True)


def test_same_license_number_in_another_state_does_not_raise_the_level() -> None:
    own = License(number="0012345", state="NJ")
    listed = License(number="0012345", state="NY")

    result = match_provider_to_record(individual(licenses=(own,)), exclusion(license=listed))

    assert result.level is MatchLevel.POTENTIAL


def test_different_date_of_birth_rules_out_a_name_match() -> None:
    result = match_provider_to_record(
        individual(date_of_birth=BIRTH), exclusion(date_of_birth=date(1981, 2, 3))
    )

    assert result.level is MatchLevel.NONE
    assert result.evidence[-1] == MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=False)
    assert "date of birth differs" in result.explanation


def test_equal_middle_names_are_high() -> None:
    result = match_provider_to_record(individual(middle="Jordan"), exclusion(middle="JORDAN"))

    assert result.level is MatchLevel.HIGH
    assert result.evidence[-1] == MatchEvidence(field=MatchField.MIDDLE_NAME, matched=True)
    assert result.explanation == (
        "High-confidence match: last name matches, first name matches, middle name matches."
    )


def test_a_middle_initial_matching_the_middle_name_is_high() -> None:
    result = match_provider_to_record(individual(middle="Jordan"), exclusion(middle="J."))

    assert result.level is MatchLevel.HIGH
    assert result.evidence[-1] == MatchEvidence(
        field=MatchField.MIDDLE_NAME, matched=True, detail="initial only"
    )


@pytest.mark.parametrize(("own", "listed"), [("Jordan", "Riley"), ("J", "R."), ("Jordan", "R")])
def test_conflicting_middle_names_are_ambiguous(own: str, listed: str) -> None:
    result = match_provider_to_record(individual(middle=own), exclusion(middle=listed))

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.evidence[-1] == MatchEvidence(field=MatchField.MIDDLE_NAME, matched=False)


def test_a_license_does_not_outweigh_conflicting_middle_names() -> None:
    own = License(number="0012345", state="NY")
    provider = individual(middle="Jordan", licenses=(own,))

    result = match_provider_to_record(provider, exclusion(middle="Riley", license=own))

    assert result.level is MatchLevel.AMBIGUOUS


def test_equal_date_of_birth_outweighs_conflicting_middle_names() -> None:
    provider = individual(middle="Jordan", date_of_birth=BIRTH)

    result = match_provider_to_record(provider, exclusion(middle="Riley", date_of_birth=BIRTH))

    assert result.level is MatchLevel.HIGH
    assert result.evidence[-2:] == (
        MatchEvidence(field=MatchField.MIDDLE_NAME, matched=False),
        MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=True),
    )


def test_different_date_of_birth_outweighs_equal_middle_names() -> None:
    provider = individual(middle="Jordan", date_of_birth=BIRTH)
    record = exclusion(middle="Jordan", date_of_birth=date(1981, 2, 3))

    assert match_provider_to_record(provider, record).level is MatchLevel.NONE


@pytest.mark.parametrize(("own", "listed"), [("Jordan", None), (None, "Riley"), ("", "J")])
def test_a_missing_middle_name_changes_nothing(own: str | None, listed: str | None) -> None:
    result = match_provider_to_record(individual(middle=own), exclusion(middle=listed))

    assert result.level is MatchLevel.POTENTIAL
    assert MatchField.MIDDLE_NAME not in {item.field for item in result.evidence}


def test_first_initial_only_is_ambiguous() -> None:
    result = match_provider_to_record(individual(first="Avery"), exclusion(first="A."))

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.evidence[-1].detail == "initial only"


def test_different_first_names_sharing_an_initial_do_not_match() -> None:
    result = match_provider_to_record(individual(first="Avery"), exclusion(first="Alexis"))

    assert result.level is MatchLevel.NONE
    assert result.evidence[-1] == MatchEvidence(field=MatchField.FIRST_NAME, matched=False)


def test_different_last_names_do_not_match() -> None:
    result = match_provider_to_record(individual(), exclusion(last="Samplename"))

    assert result.level is MatchLevel.NONE
    assert result.evidence == ()
    assert result.explanation == "No match: no identifying field matches."


def test_a_listed_alias_matches_by_name() -> None:
    record = exclusion(last="Marriedname", first="Avery", aliases=("TESTPERSON, AVERY J",))

    result = match_provider_to_record(individual(), record)

    assert result.level is MatchLevel.POTENTIAL
    assert all(item.matched and item.detail for item in result.evidence)
    assert "alias" in result.explanation


def test_credential_suffixes_and_punctuation_do_not_block_a_match() -> None:
    provider = individual(last="O'Testperson", first="Avery")
    record = exclusion(last="OTESTPERSON, JR.", first="AVERY M.D.")

    assert match_provider_to_record(provider, record).level is MatchLevel.POTENTIAL


def test_equal_organization_names_are_potential() -> None:
    record = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="SAMPLEVILLE CARE GROUP",
    )

    result = match_provider_to_record(organization("Sampleville Care Group"), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence == (MatchEvidence(field=MatchField.ORGANIZATION_NAME, matched=True),)


def test_organization_alias_matches() -> None:
    record = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Testco Holdings",
        aliases=("Sampleville Care Group",),
    )

    result = match_provider_to_record(organization("Sampleville Care Group"), record)

    assert result.level is MatchLevel.POTENTIAL


def test_an_organization_never_matches_a_person_by_name() -> None:
    record = exclusion(last="Sampleville Care Group", first="")

    assert match_provider_to_record(organization(), record).level is MatchLevel.NONE


def test_unknown_kind_provider_with_a_person_name_is_matched_as_a_person() -> None:
    provider = Provider(
        id=uuid4(),
        organization_id=uuid4(),
        entity_kind=EntityKind.UNKNOWN,
        person_name=PersonName(last="Testperson", first="Avery"),
        created_at=NOW,
    )

    assert match_provider_to_record(provider, exclusion()).level is MatchLevel.POTENTIAL


def test_matching_is_deterministic() -> None:
    provider = individual(date_of_birth=BIRTH)
    record = exclusion(date_of_birth=BIRTH)

    assert match_provider_to_record(provider, record) == match_provider_to_record(provider, record)


def test_candidate_keys_cover_names_and_alias_words() -> None:
    record = exclusion(
        last="O'Testperson", organization_name="Testco LLC", aliases=("Avery Marriedname",)
    )

    assert candidate_keys(record) == {
        "otestperson",
        "testco llc",
        "avery marriedname",
        "avery",
        "marriedname",
    }
