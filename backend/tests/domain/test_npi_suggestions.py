"""The linking rules on a party shared by exclusion records and roster providers, made-up people."""

from datetime import date

import pytest

from credentialing.domain.enums import EntityKind, MatchField, MatchLevel
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    LinkParty,
    MatchEvidence,
    NpiRecord,
    PersonName,
    Provider,
)
from credentialing.domain.policies import (
    CANDIDATE_CAP,
    NO_LICENSE_IN_STATE,
    OTHER_LICENSE_NUMBER,
    SAME_CITY,
    SAME_LICENSE,
    SAME_LICENSE_STATE,
    SAME_STATE,
    SAME_ZIP,
    link_record_to_npis,
    match_party_to_npi,
    match_record_to_npi,
    party_of_provider,
    party_of_record,
    suggest_npis,
)
from tests.synthetic import (
    exclusion,
    individual,
    make_npi,
    npi_organization,
    npi_person,
    organization,
)

NPI = make_npi("131000001")
OTHER_NPI = make_npi("131000002")
SAMPLEVILLE = Address(line1="1 Sample Way", city="Sampleville", state="NY", postal_code="99901")
ELSEWHERE = Address(city="Otherton", state="NJ", postal_code="88801")


def provider(
    *,
    licenses: tuple[License, ...] = (),
    addresses: tuple[Address, ...] = (),
    middle: str | None = None,
) -> Provider:
    return individual(middle=middle, licenses=licenses).model_copy(update={"addresses": addresses})


def evidence(
    party: LinkParty, candidate: NpiRecord, *, candidates: int = 1
) -> dict[MatchField, MatchEvidence]:
    result = match_party_to_npi(party, candidate, candidates=candidates)
    return {item.field: item for item in result.evidence}


# --- The record's party is what the record rules saw -----------------------------------------

RECORDS: list[ExclusionRecord] = [
    exclusion(),
    exclusion(first="A.", middle="Jordan"),
    exclusion(last="Marriedname", aliases=("Testperson, Avery J",)),
    exclusion(npi=NPI),
    exclusion(license=License(number="036-123456", state="NY")).model_copy(
        update={"address": SAMPLEVILLE}
    ),
    exclusion(
        entity_kind=EntityKind.ORGANIZATION,
        last=None,
        organization_name="Sampleville Care Group, Inc.",
        aliases=("Sampleville Clinic",),
    ).model_copy(update={"address": ELSEWHERE}),
]
CANDIDATES: list[NpiRecord] = [
    npi_person(NPI, practice=SAMPLEVILLE, licenses=(("036123456", "NY"),)),
    npi_person(OTHER_NPI, middle="J", mailing=ELSEWHERE),
    npi_organization(make_npi("131000003"), practice=SAMPLEVILLE),
]


@pytest.mark.parametrize("record", RECORDS)
def test_a_record_party_scores_exactly_as_the_record(record: ExclusionRecord) -> None:
    party = party_of_record(record)

    for found in (1, 3, CANDIDATE_CAP + 1):
        for candidate in CANDIDATES:
            assert match_party_to_npi(party, candidate, candidates=found) == (
                match_record_to_npi(record, candidate, candidates=found)
            )
        assert suggest_npis(party, CANDIDATES, found) == link_record_to_npis(
            record, CANDIDATES, found
        )


def test_a_record_party_carries_its_names_npi_and_its_one_licence_and_address() -> None:
    licence = License(number="0012345", state="NY")
    record = exclusion(aliases=("Sampleford, Avery",), npi=NPI, license=licence).model_copy(
        update={"address": SAMPLEVILLE}
    )

    assert party_of_record(record) == LinkParty(
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=record.person_name,
        aliases=("Sampleford, Avery",),
        npi=NPI,
        licenses=(licence,),
        addresses=(SAMPLEVILLE,),
    )
    assert party_of_record(exclusion()).licenses == ()
    assert party_of_record(exclusion()).addresses == ()


def test_a_provider_party_has_every_licence_and_address_and_no_aliases() -> None:
    licences = (License(number="1", state="NY"), License(number="2", state="NJ"))
    roster = provider(licenses=licences, addresses=(SAMPLEVILLE, ELSEWHERE))

    party = party_of_provider(roster)

    assert party == LinkParty(
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Testperson", first="Avery"),
        licenses=licences,
        addresses=(SAMPLEVILLE, ELSEWHERE),
    )
    business = party_of_provider(organization())
    assert (business.entity_kind, business.organization_name) == (
        EntityKind.ORGANIZATION,
        "Sampleville Care Group",
    )


# --- Several licences and addresses ----------------------------------------------------------


def test_a_provider_with_two_licences_matches_on_either() -> None:
    party = party_of_provider(
        provider(licenses=(License(number="111", state="NY"), License(number="222", state="NJ")))
    )

    for number, state in (("111", "NY"), ("222", "NJ")):
        candidate = npi_person(NPI, licenses=((number, state),))
        result = match_party_to_npi(party, candidate, candidates=2)

        assert result.level is MatchLevel.HIGH
        assert evidence(party, candidate)[MatchField.LICENSE] == MatchEvidence(
            field=MatchField.LICENSE, matched=True, detail="same state and number"
        )


def test_without_a_matching_licence_the_first_comparison_is_kept() -> None:
    party = party_of_provider(
        provider(licenses=(License(number="111", state="NY"), License(number="222", state="NJ")))
    )
    candidate = npi_person(NPI, licenses=(("999", "NY"), ("888", "NJ")))

    assert evidence(party, candidate)[MatchField.LICENSE].detail == "another number in NY"
    assert match_party_to_npi(party, candidate, candidates=1).level is MatchLevel.AMBIGUOUS


def test_two_addresses_with_one_zip_agreeing_match_and_never_contradict() -> None:
    party = party_of_provider(provider(addresses=(ELSEWHERE, SAMPLEVILLE)))
    candidate = npi_person(NPI, practice=SAMPLEVILLE)

    result = match_party_to_npi(party, candidate, candidates=3)

    assert result.level is MatchLevel.HIGH
    assert evidence(party, candidate, candidates=3)[MatchField.ADDRESS] == MatchEvidence(
        field=MatchField.ADDRESS, matched=True, detail=SAME_ZIP
    )


@pytest.mark.parametrize(
    ("addresses", "expected"),
    [
        # The strongest match wins, whichever address gives it.
        ((Address(state="NY"), SAMPLEVILLE), (True, SAME_ZIP)),
        ((Address(city="Sampleville", state="NY"), Address(state="NY")), (True, SAME_CITY)),
        ((ELSEWHERE, Address(state="NY")), (True, SAME_STATE)),
        # A match wins over a difference; without one, the first comparison made.
        ((Address(state="NJ"), Address(state="CT")), (False, None)),
        # An address with nothing to compare is skipped.
        ((Address(line1="2 Sample Way"), ELSEWHERE), (False, None)),
    ],
)
def test_the_best_address_evidence_is_kept(
    addresses: tuple[Address, ...], expected: tuple[bool, str | None]
) -> None:
    party = party_of_provider(provider(addresses=addresses))

    found = evidence(party, npi_person(NPI, practice=SAMPLEVILLE))[MatchField.ADDRESS]

    assert (found.matched, found.detail) == expected


def test_addresses_with_nothing_to_compare_give_no_address_evidence() -> None:
    party = party_of_provider(provider(addresses=(Address(line1="2 Sample Way"),)))

    assert MatchField.ADDRESS not in evidence(party, npi_person(NPI, practice=SAMPLEVILLE))


def test_a_provider_party_is_never_exact_and_its_birth_date_is_not_compared() -> None:
    licence = License(number="0012345", state="NY")
    roster = provider(licenses=(licence,), addresses=(SAMPLEVILLE,), middle="Jordan").model_copy(
        update={"date_of_birth": date(1970, 1, 1)}
    )
    candidate = npi_person(NPI, middle="J", practice=SAMPLEVILLE, licenses=(("0012345", "NY"),))

    ((npi, result),) = suggest_npis(party_of_provider(roster), [candidate], 1)

    assert (npi, result.level) == (NPI, MatchLevel.HIGH)
    assert MatchField.DATE_OF_BIRTH not in {item.field for item in result.evidence}


def test_an_organization_provider_is_matched_by_name_and_place() -> None:
    party = party_of_provider(
        organization("Sampleville Care Group").model_copy(update={"addresses": (SAMPLEVILLE,)})
    )

    ((npi, result),) = suggest_npis(party, [npi_organization(NPI, practice=SAMPLEVILLE)], 1)

    assert (npi, result.level) == (NPI, MatchLevel.HIGH)


# --- Gap reasons -----------------------------------------------------------------------------


def _gap(party: LinkParty, candidates: list[NpiRecord], found: int) -> str:
    ((npi, result),) = suggest_npis(party, candidates, found)
    assert (npi, result.level) == (None, MatchLevel.NONE)
    return result.explanation


def test_a_provider_without_candidates_says_why() -> None:
    party = party_of_provider(provider())

    assert _gap(party, [], 0) == "No match: no candidate with this name."
    assert _gap(party, [npi_person(NPI, first="RILEY")], 1) == (
        "No match: no candidate matches the name."
    )
    many = [npi_person(make_npi(f"1320000{n:02d}")) for n in range(CANDIDATE_CAP)]
    assert _gap(party, many, CANDIDATE_CAP + 1) == f"No match: more than {CANDIDATE_CAP} namesakes."


def test_a_provider_without_a_first_name_says_so() -> None:
    party = party_of_provider(provider()).model_copy(
        update={"person_name": PersonName(last="Testperson", first="")}
    )

    assert _gap(party, [], 0) == "No match: no first name to search with."


# --- A licence known by its state alone (owner, 2026-11-12) ----------------------------------

STATE_ONLY_NY = License(state="NY")


def _party(*licenses: License, first: str = "Avery") -> LinkParty:
    return party_of_provider(individual(first=first, licenses=licenses))


def test_a_state_only_licence_matches_a_candidate_licensed_in_that_state() -> None:
    party = _party(STATE_ONLY_NY)
    candidate = npi_person(NPI, licenses=(("88-1", "NJ"), ("0012345", "NY")))

    assert evidence(party, candidate)[MatchField.LICENSE] == MatchEvidence(
        field=MatchField.LICENSE, matched=True, detail=SAME_LICENSE_STATE
    )


def test_a_state_only_licence_against_other_states_says_so() -> None:
    party = _party(STATE_ONLY_NY)
    candidate = npi_person(NPI, licenses=(("88-1", "NJ"),))

    assert evidence(party, candidate)[MatchField.LICENSE] == MatchEvidence(
        field=MatchField.LICENSE,
        matched=False,
        detail=NO_LICENSE_IN_STATE.format(state="NY"),
    )
    assert NO_LICENSE_IN_STATE.format(state="NY") == "no licence in NY in the registry"


def test_a_state_only_licence_without_registry_licences_gives_no_evidence() -> None:
    assert MatchField.LICENSE not in evidence(_party(STATE_ONLY_NY), npi_person(NPI))


@pytest.mark.parametrize("first", ["Avery", "A"])
@pytest.mark.parametrize("candidates", [1, 3])
@pytest.mark.parametrize("licensed_in", ["NY", "NJ"])
def test_a_state_only_licence_never_changes_the_level(
    first: str, candidates: int, licensed_in: str
) -> None:
    candidate = npi_person(NPI, licenses=(("0012345", licensed_in),))

    alone = match_party_to_npi(_party(first=first), candidate, candidates=candidates)
    with_state = match_party_to_npi(
        _party(STATE_ONLY_NY, first=first), candidate, candidates=candidates
    )

    assert with_state.level is alone.level
    assert MatchField.LICENSE in {item.field for item in with_state.evidence}


def test_a_name_only_match_with_a_matching_state_stays_potential() -> None:
    candidate = npi_person(NPI, licenses=(("0012345", "NY"),))

    result = match_party_to_npi(_party(STATE_ONLY_NY), candidate, candidates=1)

    assert result.level is MatchLevel.POTENTIAL


def test_only_the_same_number_is_strong() -> None:
    candidate = npi_person(NPI, licenses=(("0012345", "NY"),))

    numbered = match_party_to_npi(
        _party(License(number="001-2345", state="NY"), first="A"), candidate, candidates=3
    )
    state_only = match_party_to_npi(_party(STATE_ONLY_NY, first="A"), candidate, candidates=3)

    assert (
        evidence(_party(License(number="0012345", state="NY")), candidate)[
            MatchField.LICENSE
        ].detail
        == SAME_LICENSE
    )
    assert (numbered.level, state_only.level) == (MatchLevel.POTENTIAL, MatchLevel.AMBIGUOUS)


def test_a_numbered_licence_speaks_before_a_state_alone() -> None:
    party = _party(License(number="111", state="NY"), License(state="NJ"))
    candidate = npi_person(NPI, licenses=(("999", "NY"), ("5", "NJ")))

    assert evidence(party, candidate)[MatchField.LICENSE].detail == (
        OTHER_LICENSE_NUMBER.format(state="NY")
    )
    assert match_party_to_npi(party, candidate, candidates=1).level is MatchLevel.AMBIGUOUS


def test_a_state_alone_never_hides_a_numbered_difference() -> None:
    numbered = License(number="111", state="NY")
    candidate = npi_person(NPI, licenses=(("5", "NJ"),))

    with_state = match_party_to_npi(_party(numbered, License(state="NJ")), candidate, candidates=1)

    assert with_state == match_party_to_npi(_party(numbered), candidate, candidates=1)
    assert with_state.level is MatchLevel.AMBIGUOUS
    assert evidence(_party(License(state="NJ")), candidate)[MatchField.LICENSE].detail == (
        SAME_LICENSE_STATE
    )


def test_a_matching_licence_state_breaks_a_tie_between_suggestions() -> None:
    first, second = make_npi("131000011"), make_npi("131000012")
    candidates = [
        npi_person(first, licenses=(("5", "NJ"),)),
        npi_person(second, licenses=(("6", "NY"),)),
    ]

    plain = suggest_npis(_party(), candidates, 2)
    licensed = suggest_npis(_party(STATE_ONLY_NY), candidates, 2)

    assert [(npi, match.level) for npi, match in plain] == [
        (first, MatchLevel.AMBIGUOUS),
        (second, MatchLevel.AMBIGUOUS),
    ]
    assert [(npi, match.level) for npi, match in licensed] == [
        (second, MatchLevel.AMBIGUOUS),
        (first, MatchLevel.AMBIGUOUS),
    ]


def test_a_matching_licence_state_never_outranks_a_better_level() -> None:
    best, other = make_npi("131000021"), make_npi("131000022")
    candidates = [
        npi_person(best, practice=SAMPLEVILLE),
        npi_person(other, licenses=(("6", "NY"),)),
    ]
    party = party_of_provider(
        individual(licenses=(STATE_ONLY_NY,)).model_copy(update={"addresses": (SAMPLEVILLE,)})
    )

    assert [npi for npi, _ in suggest_npis(party, candidates, 2)] == [best, other]
