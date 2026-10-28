import pytest

from credentialing.connectors.framework.names import (
    looks_like_business,
    parse_name_field,
    split_single_name_field,
)
from credentialing.domain.enums import EntityKind
from credentialing.domain.models import PersonName


def test_credential_after_padding_becomes_the_suffix() -> None:
    parsed = parse_name_field("TESTPERSON AVERY J  MD")

    assert parsed.person == PersonName(last="TESTPERSON", first="AVERY", middle="J", suffix="MD")
    assert parsed.organization is None
    assert parsed.entity_kind is EntityKind.INDIVIDUAL


def test_aka_splits_off_an_alias() -> None:
    parsed = parse_name_field("SAMPLEMAN JORDAN K AKA EXAMPLEMAN JORDAN")

    assert parsed.person == PersonName(last="SAMPLEMAN", first="JORDAN", middle="K")
    assert parsed.aliases == ("EXAMPLEMAN JORDAN",)


def test_business_words_make_an_organization() -> None:
    parsed = parse_name_field("SAMPLEVILLE PHARMACY INC")

    assert parsed.person is None
    assert parsed.organization == "SAMPLEVILLE PHARMACY INC"
    assert parsed.entity_kind is EntityKind.ORGANIZATION


def test_dba_after_a_person_is_an_alias_of_that_person() -> None:
    parsed = parse_name_field("FAKENAME RILEY DBA FAKENAME FAMILY CLINIC")

    assert parsed.person == PersonName(last="FAKENAME", first="RILEY")
    assert parsed.aliases == ("FAKENAME FAMILY CLINIC",)


def test_leading_dba_names_a_business() -> None:
    parsed = parse_name_field("D/B/A SAMPLE CARE, A/K/A MOCK CARE")

    assert parsed.person is None
    assert parsed.organization == "SAMPLE CARE"
    assert parsed.aliases == ("MOCK CARE",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SMITHSON, AVERY M.D.", PersonName(last="SMITHSON", first="AVERY", suffix="M.D.")),
        ("DE LA MOCK MARIA", PersonName(last="DE LA MOCK", first="MARIA")),
        ("SAMPLE JOHN JR RN", PersonName(last="SAMPLE", first="JOHN", suffix="JR RN")),
        ("DO AVERY", PersonName(last="DO", first="AVERY")),
        ("TESTPERSON", PersonName(last="TESTPERSON", first="")),
    ],
)
def test_person_shapes(text: str, expected: PersonName) -> None:
    assert parse_name_field(text).person == expected


def test_organization_hint_applies_only_without_a_personal_credential() -> None:
    assert parse_name_field("MOCKWELL QUINN", organization_hint=True).organization == (
        "MOCKWELL QUINN"
    )
    hinted_person = parse_name_field("MOCKWELL QUINN RPH", organization_hint=True)
    assert hinted_person.person == PersonName(last="MOCKWELL", first="QUINN", suffix="RPH")


def test_alias_markers_inside_words_are_not_split() -> None:
    parsed = parse_name_field("SHADBAL AKASH")

    assert parsed.person == PersonName(last="SHADBAL", first="AKASH")
    assert parsed.aliases == ()


def test_empty_cells_parse_to_nothing() -> None:
    assert parse_name_field("   ").entity_kind is EntityKind.UNKNOWN
    assert parse_name_field(None).entity_kind is EntityKind.UNKNOWN


def test_split_single_name_field_is_the_person_view() -> None:
    assert split_single_name_field("SAMPLE & MOCK ASSOCIATES") == (None, ())
    person, aliases = split_single_name_field("EXAMPLE CASEY FKA SAMPLE CASEY")
    assert person == PersonName(last="EXAMPLE", first="CASEY")
    assert aliases == ("SAMPLE CASEY",)


def test_looks_like_business() -> None:
    assert looks_like_business("A&B Transport")
    assert looks_like_business("Sampleville Medical, P.C.")
    assert not looks_like_business("Testperson Avery PA")
    assert not looks_like_business(None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "SAMPLEPERSON AVERY MD PC",
            PersonName(last="SAMPLEPERSON", first="AVERY", suffix="MD PC"),
        ),
        (
            "MOCK JAMIE R DDS DENTAL P.C.",
            PersonName(last="MOCK", first="JAMIE", middle="R", suffix="DDS DENTAL P.C."),
        ),
        ("SAMPLE, CASEY DPM PLLC", PersonName(last="SAMPLE", first="CASEY", suffix="DPM PLLC")),
        ("EXAMPLE RILEY MD INC", PersonName(last="EXAMPLE", first="RILEY", suffix="MD INC")),
        (
            "SAMPLEPERSON AVERY J PC      MD",
            PersonName(last="SAMPLEPERSON", first="AVERY", middle="J", suffix="PC MD"),
        ),
    ],
)
def test_a_professional_corporation_of_one_person_is_that_person(
    text: str, expected: PersonName
) -> None:
    parsed = parse_name_field(text, organization_hint=True)

    assert parsed.entity_kind is EntityKind.INDIVIDUAL
    assert parsed.person == expected
    assert parsed.aliases == (" ".join(text.split()),)


@pytest.mark.parametrize(
    "text",
    [
        "ACME MEDICAL PC",
        "SAMPLE MD PC",
        "SAMPLE AVERY MEDICAL GROUP MD PC",
        "SAMPLEPERSON AVERY PLLC",
    ],
)
def test_a_corporation_without_a_named_professional_stays_an_organization(text: str) -> None:
    parsed = parse_name_field(text)

    assert parsed.entity_kind is EntityKind.ORGANIZATION
    assert parsed.organization == text
    assert parsed.aliases == ()


def test_a_professional_corporation_keeps_its_other_aliases() -> None:
    parsed = parse_name_field("SAMPLEPERSON AVERY MD PC DBA SAMPLE FAMILY CARE")

    assert parsed.person == PersonName(last="SAMPLEPERSON", first="AVERY", suffix="MD PC")
    assert parsed.aliases == ("SAMPLEPERSON AVERY MD PC", "SAMPLE FAMILY CARE")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "JORDAN LEE EXAMPLEPERSON",
            PersonName(last="EXAMPLEPERSON", first="JORDAN", middle="LEE"),
        ),
        ("QUINN DE LA MOCK", PersonName(last="DE LA MOCK", first="QUINN")),
        (
            "AVERY J TESTPERSON JR",
            PersonName(last="TESTPERSON", first="AVERY", middle="J", suffix="JR"),
        ),
        ("Casey Mc Example", PersonName(last="Mc Example", first="Casey")),
        ("TESTPERSON, AVERY", PersonName(last="TESTPERSON", first="AVERY")),
    ],
)
def test_first_name_first(text: str, expected: PersonName) -> None:
    assert parse_name_field(text, first_name_first=True).person == expected


@pytest.mark.parametrize(
    ("text", "person", "aliases"),
    [
        (
            "CASEY WREN SAMPLEWOMAN (MAIDENMOCK)",
            PersonName(last="SAMPLEWOMAN", first="CASEY", middle="WREN"),
            ("MAIDENMOCK",),
        ),
        (
            "Exampleperson (aka Mockname), Jordan",
            PersonName(last="Exampleperson", first="Jordan"),
            ("Mockname, Jordan",),
        ),
        (
            "Samplewoman (Mockone, Mocktwo), Casey",
            PersonName(last="Samplewoman", first="Casey"),
            ("Mockone, Casey", "Mocktwo, Casey"),
        ),
        ("Testperson, Melinda (Mindy)", PersonName(last="Testperson", first="Melinda"), ("Mindy",)),
        ("Testperson, Avery (LPN)", PersonName(last="Testperson", first="Avery", suffix="LPN"), ()),
        (
            "AVERY TESTPERSON (DBA: SAMPLE HOME CARE)",
            PersonName(last="TESTPERSON", first="AVERY"),
            ("SAMPLE HOME CARE",),
        ),
    ],
)
def test_parenthesized_names_are_aliases(
    text: str, person: PersonName, aliases: tuple[str, ...]
) -> None:
    parsed = parse_name_field(text, first_name_first="," not in text)

    assert parsed.person == person
    assert parsed.aliases == aliases


def test_a_parenthesized_dba_of_a_business_is_its_alias() -> None:
    parsed = parse_name_field("SAMPLE & COMPANY LLC (DBA: MOCK\nMEDICAL)")

    assert parsed.organization == "SAMPLE & COMPANY LLC"
    assert parsed.aliases == ("MOCK MEDICAL",)


@pytest.mark.parametrize(
    "text",
    ["SAMPLE OBSTETRICS AND GYNECOLOGY", "MOCK COUNTY MENTAL HEALTH", "CLASSIC HOMES BY MOCK BROS"],
)
def test_and_county_and_homes_are_business_words(text: str) -> None:
    assert parse_name_field(text).organization == text


@pytest.mark.parametrize(
    ("text", "suffix"),
    [
        ("Testperson, Avery M ARNP", "ARNP"),
        ("Example, Quinn PA-C", "PA-C"),
        ("Exampleowner, Jordan L. DN", None),
        ("EXAMPLE,JORDAN LCMHC", "LCMHC"),
        ("Mockdoc, Riley K. MD/", "MD/"),
    ],
)
def test_state_list_credentials_are_suffixes(text: str, suffix: str | None) -> None:
    person = parse_name_field(text).person

    assert person is not None
    assert person.suffix == suffix
