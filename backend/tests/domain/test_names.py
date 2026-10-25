import pytest

from credentialing.domain.names import normalize_name


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
