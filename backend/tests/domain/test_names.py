import pytest

from credentialing.domain.names import (
    NameSearchShape,
    NameTerm,
    name_search_shapes,
    normalize_name,
    normalize_organization_name,
)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("  TESTPERSON  ", "testperson"),
        ("O'Testperson", "otestperson"),
        ("O\u2019Testperson", "otestperson"),
        ("Testperson-Sample", "testperson sample"),
        ("Testperson,   Avery", "testperson avery"),
        ("Avery Testperson M.D.", "avery testperson"),
        ("Avery Testperson, Jr., PhD", "avery testperson"),
        ("Testperson III", "testperson"),
        ("Zoë Núñez", "zoe nunez"),
        ("Do", "do"),
        ("Pa", "pa"),
        ("", ""),
    ],
)
def test_normalize_name(raw: str, normalized: str) -> None:
    assert normalize_name(raw) == normalized


def test_suffix_inside_a_name_is_kept() -> None:
    assert normalize_name("Md Testperson") == "md testperson"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("7 STAR SAMPLEVILLE PHARMACY AND DELI INC", "7 star sampleville pharmacy and deli"),
        ("Acme Co", "acme"),
        ("Acme Corp.", "acme"),
        ("Acme Corporation", "acme"),
        ("The Acme Group LLC", "acme group"),
        ("Acme, L.L.C.", "acme"),
        ("Acme Co Inc", "acme"),
        ("Acme Holdings Ltd", "acme holdings"),
        ("Sampleville Therapy PLLC", "sampleville therapy"),
        ("Acme Company Pharmacy", "acme company pharmacy"),
        ("Theater Supply Co", "theater supply"),
        ("Company", "company"),
        ("The Company", "the company"),
        ("Inc", "inc"),
        ("", ""),
    ],
)
def test_normalize_organization_name(raw: str, normalized: str) -> None:
    assert normalize_organization_name(raw) == normalized


def _prefix(text: str) -> NameTerm:
    return NameTerm(text, prefix=True)


def test_no_words_and_a_one_letter_word_search_nothing() -> None:
    assert name_search_shapes([]) == ()
    assert name_search_shapes(["t"]) == ()


def test_one_word_is_the_start_of_a_last_name() -> None:
    assert name_search_shapes(["te"]) == (NameSearchShape(last=_prefix("te")),)
    assert name_search_shapes(["testperson"]) == (NameSearchShape(last=_prefix("testperson")),)


def test_two_words_read_last_first_and_first_last_with_the_typed_word_as_a_prefix() -> None:
    assert name_search_shapes(["avery", "te"]) == (
        NameSearchShape(last=NameTerm("avery"), first=_prefix("te")),
        NameSearchShape(last=_prefix("te"), first=NameTerm("avery")),
    )


def test_a_one_letter_last_word_is_only_a_first_name_initial() -> None:
    assert name_search_shapes(["testperson", "a"]) == (
        NameSearchShape(last=NameTerm("testperson"), first=_prefix("a")),
    )


def test_three_words_use_every_word() -> None:
    assert name_search_shapes(["avery", "quinn", "testp"]) == (
        NameSearchShape(
            last=_prefix("testp"), first=NameTerm("avery"), middle=(NameTerm("quinn"),)
        ),
        NameSearchShape(
            last=NameTerm("avery"), first=NameTerm("quinn"), middle=(_prefix("testp"),)
        ),
    )


def test_a_one_letter_middle_word_is_an_initial() -> None:
    assert name_search_shapes(["avery", "q", "testperson"]) == (
        NameSearchShape(
            last=_prefix("testperson"), first=NameTerm("avery"), middle=(_prefix("q"),)
        ),
        NameSearchShape(
            last=NameTerm("avery"), first=NameTerm("q"), middle=(_prefix("testperson"),)
        ),
    )


def test_a_one_letter_typed_word_after_three_words_is_only_a_middle_initial() -> None:
    assert name_search_shapes(["testperson", "avery", "q"]) == (
        NameSearchShape(
            last=NameTerm("testperson"), first=NameTerm("avery"), middle=(_prefix("q"),)
        ),
    )


def test_four_words_put_the_inner_words_in_the_middle_name() -> None:
    assert name_search_shapes(["avery", "quinn", "j", "testperson"]) == (
        NameSearchShape(
            last=_prefix("testperson"),
            first=NameTerm("avery"),
            middle=(NameTerm("quinn"), _prefix("j")),
        ),
        NameSearchShape(
            last=NameTerm("avery"),
            first=NameTerm("quinn"),
            middle=(_prefix("j"), _prefix("testperson")),
        ),
    )
