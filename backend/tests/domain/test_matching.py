from datetime import date
from uuid import uuid4

import pytest

from credentialing.domain.enums import EntityKind, MatchField, MatchLevel, MatchRelation
from credentialing.domain.models import (
    ExclusionRecord,
    License,
    MatchEvidence,
    PersonName,
    Provider,
)
from credentialing.domain.policies import (
    candidate_keys,
    match_provider_to_record,
    party_key,
    shares_party_name,
)
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


LICENSE_ONLY_NOTE = "licence numbers are unique only within a profession, so a person must confirm"


def test_a_licence_without_a_matching_name_is_ambiguous_never_none() -> None:
    licence = License(number="0012345", state="NY")
    provider = individual(last="Testperson", first="Avery", licenses=(licence,))

    result = match_provider_to_record(
        provider,
        exclusion(last="Othername", first="Blake", license=License(number="0012 345", state="NY")),
    )

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.evidence == (
        MatchEvidence(field=MatchField.LICENSE, matched=True, detail="same state and number"),
        MatchEvidence(field=MatchField.LAST_NAME, matched=False),
    )
    assert result.explanation == (
        "Ambiguous match: license matches (same state and number), last name differs; "
        f"{LICENSE_ONLY_NOTE}."
    )


def test_a_licence_with_only_the_last_name_matching_keeps_the_name_evidence() -> None:
    licence = License(number="0012345", state="NY")

    result = match_provider_to_record(
        individual(first="Avery", licenses=(licence,)), exclusion(first="Blake", license=licence)
    )

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.evidence == (
        MatchEvidence(field=MatchField.LICENSE, matched=True, detail="same state and number"),
        MatchEvidence(field=MatchField.LAST_NAME, matched=True),
        MatchEvidence(field=MatchField.FIRST_NAME, matched=False),
    )
    assert result.explanation.endswith(f"; {LICENSE_ONLY_NOTE}.")


def test_a_licence_on_a_record_without_a_person_name_is_ambiguous() -> None:
    licence = License(number="0012345", state="NY")

    result = match_provider_to_record(
        individual(licenses=(licence,)),
        exclusion(
            entity_kind=EntityKind.ORGANIZATION,
            last=None,
            organization_name="Sampleville Chiropractic",
            license=licence,
        ),
    )

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.evidence[1] == MatchEvidence(field=MatchField.LAST_NAME, matched=False)


def test_a_licence_kind_is_not_compared_yet() -> None:
    own = License(number="0012345", state="NY", kind="Chiropractor")
    listed = License(number="0012345", state="NY", kind="Massage Therapist")

    result = match_provider_to_record(
        individual(licenses=(own,)), exclusion(last="Othername", license=listed)
    )

    assert result.level is MatchLevel.AMBIGUOUS


def test_conflicting_npis_rule_out_a_licence_match() -> None:
    licence = License(number="0012345", state="NY")

    result = match_provider_to_record(
        individual(npi=NPI, licenses=(licence,)),
        exclusion(last="Othername", npi=OTHER_NPI, license=licence),
    )

    assert result.level is MatchLevel.NONE
    assert result.evidence == (MatchEvidence(field=MatchField.NPI, matched=False),)


def test_a_different_date_of_birth_rules_out_a_licence_only_match() -> None:
    licence = License(number="0012345", state="NY")

    result = match_provider_to_record(
        individual(date_of_birth=BIRTH, licenses=(licence,)),
        exclusion(last="Othername", date_of_birth=date(1981, 2, 3), license=licence),
    )

    assert result.level is MatchLevel.NONE
    assert result.evidence[-1] == MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=False)


def test_the_same_licence_number_in_another_state_without_a_name_match_is_none() -> None:
    result = match_provider_to_record(
        individual(licenses=(License(number="0012345", state="NJ"),)),
        exclusion(last="Othername", license=License(number="0012345", state="NY")),
    )

    assert result.level is MatchLevel.NONE
    assert result.evidence == ()


def test_an_organization_sharing_only_a_licence_is_ambiguous() -> None:
    licence = License(number="028123", state="NY")
    provider = organization("Sampleville Care Group").model_copy(update={"licenses": (licence,)})

    result = match_provider_to_record(
        provider,
        exclusion(
            entity_kind=EntityKind.ORGANIZATION,
            last=None,
            organization_name="Elsewhere Pharmacy",
            license=licence,
        ),
    )

    assert result.level is MatchLevel.AMBIGUOUS
    assert result.evidence == (
        MatchEvidence(field=MatchField.LICENSE, matched=True, detail="same state and number"),
        MatchEvidence(field=MatchField.ORGANIZATION_NAME, matched=False),
    )
    assert result.explanation.endswith(f"; {LICENSE_ONLY_NOTE}.")


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


def test_a_legal_suffix_does_not_block_an_organization_match() -> None:
    record = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="7 STAR SAMPLEVILLE PHARMACY AND DELI INC",
    )

    result = match_provider_to_record(organization("7 Star Sampleville Pharmacy and Deli"), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence == (
        MatchEvidence(
            field=MatchField.ORGANIZATION_NAME, matched=True, detail="legal suffix ignored"
        ),
    )
    assert "legal suffix ignored" in result.explanation


@pytest.mark.parametrize(
    ("own", "listed"),
    [("Acme Co", "Acme Corp"), ("The Acme Group LLC", "Acme Group"), ("Acme, L.L.C.", "ACME")],
)
def test_organizations_differing_only_in_legal_form_match(own: str, listed: str) -> None:
    record = exclusion(entity_kind=EntityKind.ORGANIZATION, last=None, organization_name=listed)

    result = match_provider_to_record(organization(own), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0].detail == "legal suffix ignored"


def test_a_name_that_is_only_a_legal_form_is_not_stripped_into_a_match() -> None:
    record = exclusion(entity_kind=EntityKind.ORGANIZATION, last=None, organization_name="Inc")

    assert match_provider_to_record(organization("Company"), record).level is MatchLevel.NONE


def test_an_organization_alias_matches_without_its_legal_suffix() -> None:
    record = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Testco Holdings",
        aliases=("Sampleville Care Group LLC",),
    )

    result = match_provider_to_record(organization("Sampleville Care Group"), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0].detail == "alias 'Sampleville Care Group LLC'; legal suffix ignored"


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
        "testco",
        "avery marriedname",
        "avery",
        "marriedname",
    }


def test_candidate_keys_drop_legal_suffixes_from_organization_names_and_aliases() -> None:
    record = exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="The Sampleville Group, Inc.",
        aliases=("Testco LLC",),
    )

    assert candidate_keys(record) == {"sampleville group", "testco llc", "testco", "llc"}


# --- Related NPIs, related parties and NPIs of different kinds of party --------------------

BUSINESS_NPI = make_npi("100000003")


def _business_with_owner(**changes: object) -> ExclusionRecord:
    """A business row that also names its owner and publishes the owner's NPI."""
    fields: dict[str, object] = {
        "entity_kind": EntityKind.ORGANIZATION,
        "last": None,
        "organization_name": "Sample Dentures NW",
        "aliases": ("Mock Dental and Denture",),
        "related_parties": ("Testperson, Avery J. DN",),
        "npi": BUSINESS_NPI,
        "related_npis": (NPI,),
        "license": License(number="DN60000001", state="WA"),
        **changes,
    }
    return exclusion(**fields)  # type: ignore[arg-type]  # fields of mixed types


def test_a_related_npi_with_a_confirming_name_is_high_never_exact() -> None:
    result = match_provider_to_record(individual(npi=NPI), _business_with_owner())

    assert result.level is MatchLevel.HIGH
    assert result.evidence[0] == MatchEvidence(
        field=MatchField.NPI, matched=True, detail="related NPI"
    )
    assert "related party 'Testperson, Avery J. DN'" in result.explanation


def test_a_related_npi_on_a_person_row_confirmed_by_the_party_name_is_high() -> None:
    record = exclusion(npi=BUSINESS_NPI, related_npis=(NPI,))

    assert match_provider_to_record(individual(npi=NPI), record).level is MatchLevel.HIGH


def test_a_related_npi_without_a_confirming_name_is_potential() -> None:
    provider = individual(last="Othername", first="Blake", npi=NPI)

    result = match_provider_to_record(provider, _business_with_owner())

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence == (
        MatchEvidence(field=MatchField.NPI, matched=True, detail="related NPI; name not confirmed"),
    )
    assert "a person must confirm" in result.explanation


def test_an_initial_only_name_does_not_confirm_a_related_npi() -> None:
    record = exclusion(first="A", npi=BUSINESS_NPI, related_npis=(NPI,))

    result = match_provider_to_record(individual(npi=NPI), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0].detail == "related NPI; name not confirmed"


def test_differing_npis_of_different_kinds_fall_through_to_the_names() -> None:
    record = _business_with_owner(related_npis=())

    result = match_provider_to_record(individual(npi=OTHER_NPI), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0] == MatchEvidence(
        field=MatchField.NPI,
        matched=False,
        detail="the NPIs of a person and of an organization never match",
    )


def test_an_organization_provider_with_its_own_npi_matches_a_person_row_by_alias() -> None:
    record = exclusion(npi=NPI, aliases=("Sampleville Care Group",))

    result = match_provider_to_record(organization(npi=BUSINESS_NPI), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0].matched is False


def test_differing_npis_of_the_same_kind_are_still_no_match() -> None:
    record = _business_with_owner(related_npis=())

    result = match_provider_to_record(organization("Sample Dentures NW", npi=OTHER_NPI), record)

    assert result.level is MatchLevel.NONE
    assert result.evidence == (MatchEvidence(field=MatchField.NPI, matched=False),)


def test_a_related_party_name_alone_is_potential() -> None:
    result = match_provider_to_record(individual(), _business_with_owner(related_npis=()))

    assert result.level is MatchLevel.POTENTIAL
    assert all(item.detail == "related party 'Testperson, Avery J. DN'" for item in result.evidence)


def test_a_related_party_name_and_the_same_licence_stay_potential() -> None:
    licence = License(number="DN60000001", state="WA")
    provider = individual(middle="J", date_of_birth=BIRTH, licenses=(licence,))
    record = _business_with_owner(related_npis=(), date_of_birth=BIRTH)

    result = match_provider_to_record(provider, record)

    assert result.level is MatchLevel.POTENTIAL
    assert MatchField.LICENSE not in {item.field for item in result.evidence}


def test_an_organization_named_as_a_related_party_is_potential() -> None:
    record = exclusion(npi=NPI, related_parties=("Sampleville Care Group LLC",))

    result = match_provider_to_record(organization(), record)

    assert result.level is MatchLevel.POTENTIAL
    assert result.evidence[0].detail == (
        "related party 'Sampleville Care Group LLC'; legal suffix ignored"
    )


def test_an_alias_still_matches_as_the_same_party() -> None:
    licence = License(number="0012345", state="NY")
    record = exclusion(last="Marriedname", aliases=("Testperson, Avery",), license=licence)

    result = match_provider_to_record(individual(licenses=(licence,)), record)

    assert result.level is MatchLevel.HIGH


def test_candidate_keys_cover_related_parties() -> None:
    record = _business_with_owner()

    assert {"testperson avery j dn", "testperson", "avery"} <= candidate_keys(record)


def test_shares_party_name_compares_people_by_last_and_first_name() -> None:
    record = exclusion(first="Avery")

    assert shares_party_name(record, exclusion(last="TESTPERSON", first="avery"))
    assert shares_party_name(record, exclusion(first="A"))
    assert shares_party_name(record, exclusion(last="Other", aliases=("Avery Testperson",)))
    assert shares_party_name(record, _business_with_owner())
    assert not shares_party_name(record, exclusion(first="Blake"))
    assert not shares_party_name(record, exclusion(last="Other"))


def test_shares_party_name_compares_organizations_without_legal_designators() -> None:
    record = _business_with_owner()

    def business(name: str) -> ExclusionRecord:
        return exclusion(entity_kind=EntityKind.ORGANIZATION, last=None, organization_name=name)

    assert shares_party_name(record, business("Sample Dentures NW LLC"))
    assert shares_party_name(record, exclusion(related_parties=("Sample Dentures NW",)))
    assert not shares_party_name(record, business("Sample Labs"))


def test_the_first_npi_of_a_shared_row_for_another_kind_of_party_needs_a_name() -> None:
    person_row = exclusion(npi=BUSINESS_NPI, related_npis=(NPI,))

    unnamed = match_provider_to_record(organization(npi=BUSINESS_NPI), person_row)
    named = match_provider_to_record(
        organization("Sample Family Care", npi=BUSINESS_NPI),
        person_row.model_copy(update={"related_parties": ("Sample Family Care",)}),
    )

    assert unnamed.level is MatchLevel.POTENTIAL
    assert unnamed.evidence[0].detail == (
        "first of several NPIs on the row, listed for another kind of party; name not confirmed"
    )
    assert named.level is MatchLevel.HIGH


def test_the_first_npi_of_a_shared_row_for_the_same_kind_of_party_is_exact() -> None:
    person_row = exclusion(npi=NPI, related_npis=(BUSINESS_NPI,))

    assert match_provider_to_record(individual(npi=NPI), person_row).level is MatchLevel.EXACT


def test_the_record_npi_of_a_single_npi_row_is_exact_for_any_kind() -> None:
    person_row = exclusion(npi=NPI)

    assert match_provider_to_record(organization(npi=NPI), person_row).level is MatchLevel.EXACT


def test_party_key_is_the_last_name_or_the_organization_name() -> None:
    assert party_key(exclusion(last="O'Testperson")) == "otestperson"
    assert party_key(_business_with_owner()) == "sample dentures nw"
    assert party_key(exclusion(last=None)) is None


def test_a_person_with_the_first_npi_of_a_business_row_needs_a_name() -> None:
    owner_first = _business_with_owner(npi=NPI, related_npis=(BUSINESS_NPI,))

    named = match_provider_to_record(individual(npi=NPI), owner_first)
    unnamed = match_provider_to_record(
        individual(last="Othername", first="Blake", npi=NPI), owner_first
    )

    assert named.level is MatchLevel.HIGH
    assert named.evidence[0] == MatchEvidence(
        field=MatchField.NPI,
        matched=True,
        detail="first of several NPIs on the row, listed for another kind of party",
    )
    assert "related party 'Testperson, Avery J. DN'" in named.explanation
    assert unnamed.level is MatchLevel.POTENTIAL
    assert unnamed.evidence == (
        MatchEvidence(
            field=MatchField.NPI,
            matched=True,
            detail=(
                "first of several NPIs on the row, listed for another kind of party; "
                "name not confirmed"
            ),
        ),
    )


# --- Relation: the role on the row the provider was matched as ------------------------------

PARTY = MatchRelation.PARTY
RELATED = MatchRelation.RELATED_PARTY


@pytest.mark.parametrize(
    ("provider", "record", "level"),
    [
        (individual(npi=NPI), exclusion(last="Othername", first="Blake", npi=NPI), "exact"),
        (individual(), exclusion(), "potential"),
        (individual(date_of_birth=BIRTH), exclusion(date_of_birth=BIRTH), "high"),
        (individual(), exclusion(last="Marriedname", aliases=("Testperson, Avery",)), "potential"),
        (organization(), exclusion(last=None, aliases=("Sampleville Care Group",)), "potential"),
    ],
)
def test_a_match_through_the_party_or_an_alias_is_the_party(
    provider: Provider, record: ExclusionRecord, level: str
) -> None:
    result = match_provider_to_record(provider, record)

    assert result.level is MatchLevel(level)
    assert result.relation is PARTY


def test_a_related_party_name_alone_is_a_related_party_match() -> None:
    person = match_provider_to_record(individual(), _business_with_owner(related_npis=()))
    business = match_provider_to_record(
        organization(), exclusion(npi=NPI, related_parties=("Sampleville Care Group LLC",))
    )

    assert (person.level, person.relation) == (MatchLevel.POTENTIAL, RELATED)
    assert (business.level, business.relation) == (MatchLevel.POTENTIAL, RELATED)


def test_a_related_npi_takes_the_relation_of_the_confirming_name() -> None:
    by_related_party = match_provider_to_record(individual(npi=NPI), _business_with_owner())
    by_party = match_provider_to_record(
        individual(npi=NPI), exclusion(npi=BUSINESS_NPI, related_npis=(NPI,))
    )
    unconfirmed = match_provider_to_record(
        individual(last="Othername", first="Blake", npi=NPI), _business_with_owner()
    )

    assert (by_related_party.level, by_related_party.relation) == (MatchLevel.HIGH, RELATED)
    assert (by_party.level, by_party.relation) == (MatchLevel.HIGH, PARTY)
    assert (unconfirmed.level, unconfirmed.relation) == (MatchLevel.POTENTIAL, PARTY)


def test_the_first_npi_of_a_shared_row_named_as_a_related_party_is_a_related_party() -> None:
    person_row = exclusion(
        npi=BUSINESS_NPI, related_npis=(NPI,), related_parties=("Sample Family Care",)
    )
    owner_first = _business_with_owner(npi=NPI, related_npis=(BUSINESS_NPI,))

    business = match_provider_to_record(
        organization("Sample Family Care", npi=BUSINESS_NPI), person_row
    )
    person = match_provider_to_record(individual(npi=NPI), owner_first)

    assert (business.level, business.relation) == (MatchLevel.HIGH, RELATED)
    assert (person.level, person.relation) == (MatchLevel.HIGH, RELATED)
