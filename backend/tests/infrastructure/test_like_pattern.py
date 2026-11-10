from credentialing.domain.names import NameTerm
from credentialing.infrastructure.repositories.npi_registry import like_pattern


def test_a_whole_word_is_matched_as_is_and_a_prefix_gets_a_wildcard() -> None:
    assert like_pattern(NameTerm("testperson")) == "testperson"
    assert like_pattern(NameTerm("test", prefix=True)) == "test%"


def test_like_wildcards_and_the_escape_character_are_escaped() -> None:
    assert like_pattern(NameTerm("a%b_c\\d")) == "a\\%b\\_c\\\\d"
    assert like_pattern(NameTerm("50%", prefix=True)) == "50\\%%"
