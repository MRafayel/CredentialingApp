"""Linking an exclusion record to NPIs of the registry mirror: one test per rule, made-up people."""

import pytest

from credentialing.domain.enums import EntityKind, MatchField, MatchLevel, NpiStatus
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    MatchEvidence,
    NpiRecord,
)
from credentialing.domain.policies import (
    CANDIDATE_CAP,
    link_record_to_npis,
    match_record_to_npi,
)
from tests.synthetic import exclusion, make_npi, npi_organization, npi_person, npi_stub

NPI = make_npi("130000001")
OTHER_NPI = make_npi("130000002")
SAMPLEVILLE = Address(line1="1 Sample Way", city="Sampleville", state="NY", postal_code="99901")


def listed_business(
    name: str = "Sampleville Care Group, Inc.", **changes: object
) -> ExclusionRecord:
    record = exclusion(entity_kind=EntityKind.ORGANIZATION, last=None, organization_name=name)
    return record.model_copy(update=changes)


def with_address(record: ExclusionRecord, address: Address) -> ExclusionRecord:
    return record.model_copy(update={"address": address})


def evidence(
    record: ExclusionRecord, candidate: NpiRecord, *, candidates: int = 1
) -> dict[MatchField, MatchEvidence]:
    result = match_record_to_npi(record, candidate, candidates=candidates)
    return {item.field: item for item in result.evidence}


def level(record: ExclusionRecord, candidate: NpiRecord, *, candidates: int = 1) -> MatchLevel:
    return match_record_to_npi(record, candidate, candidates=candidates).level


# --- Individuals: names ----------------------------------------------------------------------


def test_the_last_name_must_match() -> None:
    assert level(exclusion(), npi_person(NPI, last="OTHERPERSON")) is MatchLevel.NONE


def test_the_first_name_must_match_and_nicknames_are_not_resolved() -> None:
    assert level(exclusion(first="Avery"), npi_person(NPI, first="AVE")) is MatchLevel.NONE


def test_a_unique_namesake_is_potential_never_exact_by_name() -> None:
    result = match_record_to_npi(exclusion(), npi_person(NPI), candidates=1)

    assert result.level is MatchLevel.POTENTIAL
    assert [e.field for e in result.evidence] == [MatchField.LAST_NAME, MatchField.FIRST_NAME]
    assert result.explanation == "Potential match: last name matches, first name matches."


def test_one_hundred_namesakes_are_ambiguous_and_say_so() -> None:
    result = match_record_to_npi(
        exclusion(last="Smith", first="John"),
        npi_person(NPI, last="SMITH", first="JOHN"),
        candidates=51,
    )

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.explanation.endswith("; one of more than 50 namesakes in the registry.")


def test_several_namesakes_count_in_the_explanation() -> None:
    result = match_record_to_npi(exclusion(), npi_person(NPI), candidates=3)

    assert result.level is MatchLevel.AMBIGUOUS
    assert "one of 3 namesakes in the registry" in result.explanation


def test_an_initial_on_either_side_matches_but_never_counts_as_a_full_first_name() -> None:
    initial = exclusion(first="A.")

    assert evidence(initial, npi_person(NPI))[MatchField.FIRST_NAME].detail == "initial only"
    assert (
        evidence(exclusion(), npi_person(NPI, first="A"))[MatchField.FIRST_NAME].detail
        == "initial only"
    )
    assert level(initial, npi_person(NPI), candidates=1) is MatchLevel.AMBIGUOUS


def test_an_initial_with_a_licence_or_zip_is_potential() -> None:
    initial = with_address(exclusion(first="A"), SAMPLEVILLE)

    assert (
        level(initial, npi_person(NPI, practice=SAMPLEVILLE), candidates=4) is MatchLevel.POTENTIAL
    )


def test_an_alias_naming_the_candidate_counts_as_the_name() -> None:
    record = exclusion(last="Marriedname", aliases=("Testperson, Avery J",))

    result = match_record_to_npi(record, npi_person(NPI), candidates=1)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0] == MatchEvidence(
        field=MatchField.LAST_NAME, matched=True, detail="alias 'Testperson, Avery J'"
    )


def test_matching_middle_names_make_a_namesake_potential() -> None:
    record = exclusion(middle="Jordan")

    assert level(record, npi_person(NPI, middle="J"), candidates=5) is MatchLevel.POTENTIAL
    assert (
        evidence(record, npi_person(NPI, middle="J"))[MatchField.MIDDLE_NAME].detail
        == "initial only"
    )


def test_a_different_middle_name_is_a_contradiction() -> None:
    record = with_address(exclusion(middle="Jordan"), SAMPLEVILLE)

    assert level(record, npi_person(NPI, middle="LEE"), candidates=1) is MatchLevel.AMBIGUOUS
    assert (
        level(record, npi_person(NPI, middle="LEE", practice=SAMPLEVILLE)) is MatchLevel.AMBIGUOUS
    )


# --- Individuals: licence and address ----------------------------------------------------------


def test_the_same_licence_number_and_state_is_high() -> None:
    # Illinois publishes "036123456" where NPPES holds "036-123456" or "036.123456": only the
    # letters and digits count (owner, 2026-11-08).
    record = exclusion(license=License(number="ab 123456", state="NY"))
    candidate = npi_person(NPI, licenses=(("AB-123.456", "NY"),))

    result = match_record_to_npi(record, candidate, candidates=7)

    assert result.level is MatchLevel.HIGH
    assert evidence(record, candidate)[MatchField.LICENSE].detail == "same state and number"


def test_a_licence_the_same_by_its_digits_is_high_and_shows_both_texts() -> None:
    # New York pads to eight digits; the registry writes six, or a profession's letter first.
    record = exclusion(license=License(number="00000551", state="NY"))
    padded = npi_person(NPI, licenses=(("000551", "NY"),))
    prefixed = npi_person(NPI, licenses=(("F000551", "NY"),))

    assert level(record, padded, candidates=7) is MatchLevel.HIGH
    assert evidence(record, padded)[MatchField.LICENSE] == MatchEvidence(
        field=MatchField.LICENSE, matched=True, detail="same number 551: 00000551 and 000551"
    )
    # Three digits are too few to stand alone against a letter on one side.
    assert level(record, prefixed, candidates=1) is MatchLevel.AMBIGUOUS
    assert evidence(record, prefixed)[MatchField.LICENSE].detail == "another number in NY"


def test_the_same_digits_with_differing_parts_around_them_contradict() -> None:
    record = exclusion(license=License(number="RN123456", state="NY"))
    candidate = npi_person(NPI, licenses=(("LPN123456", "NY"), ("123456", "NJ")))

    assert level(record, candidate, candidates=1) is MatchLevel.AMBIGUOUS
    assert evidence(record, candidate)[MatchField.LICENSE] == MatchEvidence(
        field=MatchField.LICENSE,
        matched=False,
        detail="same digits 123456, but RN123456 and LPN123456 differ around them",
    )


def test_the_best_licence_comparison_speaks() -> None:
    record = exclusion(license=License(number="RN123456", state="NY"))
    candidate = npi_person(NPI, licenses=(("LPN123456", "NY"), ("000123456", "NY")))

    assert level(record, candidate, candidates=7) is MatchLevel.HIGH
    assert evidence(record, candidate)[MatchField.LICENSE].detail == (
        "same number 123456: RN123456 and 000123456"
    )


def test_a_licence_that_differs_is_a_contradiction_and_says_how() -> None:
    record = exclusion(license=License(number="123456", state="NY"))

    same_state = npi_person(NPI, licenses=(("654321", "NY"),))
    elsewhere = npi_person(NPI, licenses=(("123456", "NJ"),))

    assert level(record, same_state, candidates=1) is MatchLevel.AMBIGUOUS
    assert evidence(record, same_state)[MatchField.LICENSE].detail == "another number in NY"
    assert (
        evidence(record, elsewhere)[MatchField.LICENSE].detail == "no licence in NY in the registry"
    )


def test_no_licence_on_either_side_is_no_evidence() -> None:
    assert MatchField.LICENSE not in evidence(exclusion(), npi_person(NPI, licenses=(("1", "NY"),)))
    record = exclusion(license=License(number="123456", state="NY"))
    assert MatchField.LICENSE not in evidence(record, npi_person(NPI))


def test_the_same_zip_in_the_mailing_address_is_high() -> None:
    record = with_address(exclusion(), SAMPLEVILLE.model_copy(update={"postal_code": "99901-1234"}))
    candidate = npi_person(
        NPI,
        practice=Address(city="Elsewhere", state="NJ", postal_code="070010000"),
        mailing=Address(city="Sampleville", state="NY", postal_code="999010000"),
    )

    assert level(record, candidate, candidates=12) is MatchLevel.HIGH
    assert evidence(record, candidate)[MatchField.ADDRESS].detail == "same ZIP"


def test_the_same_city_is_potential_among_namesakes() -> None:
    record = with_address(exclusion(), SAMPLEVILLE)
    candidate = npi_person(
        NPI, practice=Address(city="SAMPLEVILLE", state="NY", postal_code="99999")
    )

    assert level(record, candidate, candidates=6) is MatchLevel.POTENTIAL
    assert evidence(record, candidate)[MatchField.ADDRESS].detail == "same city"


def test_the_same_state_only_is_evidence_but_not_enough() -> None:
    record = with_address(exclusion(), SAMPLEVILLE)
    candidate = npi_person(NPI, practice=Address(city="OTHERTOWN", state="NY", postal_code="99999"))

    assert evidence(record, candidate)[MatchField.ADDRESS].detail == "same state"
    assert level(record, candidate, candidates=6) is MatchLevel.AMBIGUOUS
    assert level(record, candidate, candidates=1) is MatchLevel.POTENTIAL


def test_an_address_that_shares_nothing_is_a_contradiction() -> None:
    record = with_address(exclusion(), SAMPLEVILLE)
    candidate = npi_person(NPI, practice=Address(city="ELSEWHERE", state="NJ", postal_code="07001"))

    assert evidence(record, candidate)[MatchField.ADDRESS].matched is False
    assert level(record, candidate, candidates=1) is MatchLevel.AMBIGUOUS


def test_the_street_never_appears_in_the_evidence() -> None:
    record = with_address(exclusion(), SAMPLEVILLE)

    result = match_record_to_npi(record, npi_person(NPI, practice=SAMPLEVILLE), candidates=1)

    assert "Sample Way" not in result.explanation


# --- Kinds, deactivated NPIs ------------------------------------------------------------------


def test_the_kinds_must_agree() -> None:
    result = match_record_to_npi(exclusion(), npi_organization(NPI), candidates=1)

    assert result.level is MatchLevel.NONE
    assert result.explanation == "No match: the kinds differ."
    assert level(listed_business(), npi_person(NPI)) is MatchLevel.NONE


def test_a_deactivated_npi_is_weighed_and_the_explanation_says_so() -> None:
    result = match_record_to_npi(
        exclusion(), npi_person(NPI, status=NpiStatus.DEACTIVATED), candidates=1
    )

    assert result.level is MatchLevel.POTENTIAL
    assert result.explanation.endswith("; the registry shows this NPI deactivated.")


def test_a_blanked_deactivated_npi_never_matches_by_name() -> None:
    assert level(exclusion(), npi_stub(NPI)) is MatchLevel.NONE


# --- Organizations ------------------------------------------------------------------------------


def test_organization_names_match_without_legal_designators() -> None:
    result = match_record_to_npi(listed_business(), npi_organization(NPI), candidates=1)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence == (
        MatchEvidence(
            field=MatchField.ORGANIZATION_NAME, matched=True, detail="legal suffix ignored"
        ),
    )


def test_an_other_name_of_the_registry_matches() -> None:
    candidate = npi_organization(
        NPI, "SAMPLE HOLDINGS LLC", other_names=("SAMPLEVILLE CARE GROUP",)
    )

    found = evidence(listed_business("Sampleville Care Group"), candidate)

    assert found[MatchField.ORGANIZATION_NAME].detail == "other name 'SAMPLEVILLE CARE GROUP'"


def test_an_alias_of_the_listed_business_matches() -> None:
    record = listed_business("Sample Holdings", aliases=("Sampleville Care Group",))

    found = evidence(record, npi_organization(NPI))

    assert found[MatchField.ORGANIZATION_NAME].detail == (
        "alias 'Sampleville Care Group'; legal suffix ignored"
    )


def test_an_organization_with_the_same_zip_is_high_and_with_the_same_city_potential() -> None:
    record = with_address(listed_business(), SAMPLEVILLE)
    same_city = Address(city="Sampleville", state="NY", postal_code="99999")

    assert (
        level(record, npi_organization(NPI, practice=SAMPLEVILLE), candidates=3) is MatchLevel.HIGH
    )
    assert (
        level(record, npi_organization(NPI, practice=same_city), candidates=3)
        is MatchLevel.POTENTIAL
    )
    assert level(record, npi_organization(NPI), candidates=3) is MatchLevel.AMBIGUOUS


def test_an_organization_whose_address_differs_stays_ambiguous() -> None:
    record = with_address(listed_business(), SAMPLEVILLE)
    elsewhere = Address(city="Elsewhere", state="NJ", postal_code="07001")

    assert (
        level(record, npi_organization(NPI, practice=elsewhere), candidates=1)
        is MatchLevel.AMBIGUOUS
    )


def test_different_organization_names_do_not_match() -> None:
    assert level(listed_business("Other Clinic"), npi_organization(NPI)) is MatchLevel.NONE


# --- Published NPIs -------------------------------------------------------------------------


def test_a_published_npi_with_the_same_last_name_is_exact() -> None:
    record = exclusion(npi=NPI, first="Avry")

    result = match_record_to_npi(record, npi_person(NPI), candidates=1)

    assert result.level is MatchLevel.EXACT
    assert result.evidence == (
        MatchEvidence(field=MatchField.NPI, matched=True, detail="published by the list"),
        MatchEvidence(field=MatchField.LAST_NAME, matched=True),
        MatchEvidence(field=MatchField.FIRST_NAME, matched=False),
    )


def test_a_published_npi_whose_name_differs_is_high_for_a_person_to_look() -> None:
    result = match_record_to_npi(
        exclusion(npi=NPI), npi_person(NPI, last="OTHERPERSON"), candidates=1
    )

    assert result.level is MatchLevel.HIGH
    assert result.evidence[1] == MatchEvidence(
        field=MatchField.LAST_NAME, matched=False, detail="name differs from the registry"
    )


def test_a_published_npi_of_the_other_kind_or_blanked_is_high_and_says_why() -> None:
    record = exclusion(npi=NPI)

    other_kind = match_record_to_npi(record, npi_organization(NPI), candidates=1)
    blanked = match_record_to_npi(record, npi_stub(NPI), candidates=1)

    assert other_kind.level is blanked.level is MatchLevel.HIGH
    assert "it lists an organization under this NPI" in other_kind.explanation
    assert "the registry publishes no name for this NPI" in blanked.explanation
    assert "deactivated" in blanked.explanation


def test_a_published_organization_npi_matches_by_organization_name() -> None:
    assert level(listed_business(npi=NPI), npi_organization(NPI)) is MatchLevel.EXACT


def test_a_published_npi_is_never_matched_to_another_npi() -> None:
    assert level(exclusion(npi=OTHER_NPI), npi_person(NPI)) is MatchLevel.NONE


# --- The links of one record ----------------------------------------------------------------


def test_links_drop_non_matches_and_come_best_first() -> None:
    record = with_address(exclusion(), SAMPLEVILLE)
    near = npi_person(OTHER_NPI, practice=SAMPLEVILLE)
    far = npi_person(NPI)
    stranger = npi_person(make_npi("130000003"), last="OTHERPERSON")

    links = link_record_to_npis(record, [far, stranger, near], 3)

    assert [(npi, match.level) for npi, match in links] == [
        (OTHER_NPI, MatchLevel.HIGH),
        (NPI, MatchLevel.AMBIGUOUS),
    ]


def test_more_than_the_cap_keeps_only_the_strong_links() -> None:
    record = with_address(exclusion(), SAMPLEVILLE)
    near = npi_person(OTHER_NPI, practice=SAMPLEVILLE)

    links = link_record_to_npis(record, [npi_person(NPI), near], CANDIDATE_CAP + 1)

    assert [(npi, match.level) for npi, match in links] == [(OTHER_NPI, MatchLevel.HIGH)]


@pytest.mark.parametrize(
    ("record", "candidates", "found", "reason"),
    [
        (exclusion(), [], 0, "no candidate with this name"),
        (exclusion(), [npi_person(NPI, first="OTHER")], 1, "no candidate matches the name"),
        (exclusion(first=""), [], 0, "no first name to search with"),
        (exclusion(npi=NPI), [], 0, "published NPI not in the registry"),
        (exclusion(), [npi_person(NPI)], CANDIDATE_CAP + 1, "more than 50 namesakes"),
        (
            exclusion(entity_kind=EntityKind.ORGANIZATION, last=None, organization_name=None),
            [],
            0,
            "no name to search with",
        ),
    ],
)
def test_a_record_left_without_a_link_gets_one_row_saying_why(
    record: ExclusionRecord, candidates: list[NpiRecord], found: int, reason: str
) -> None:
    ((npi, match),) = link_record_to_npis(record, candidates, found)

    assert npi is None
    assert match.level is MatchLevel.NONE
    assert match.explanation == f"No match: {reason}."
