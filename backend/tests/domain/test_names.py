import pytest

from credentialing.domain.names import normalize_name, normalize_organization_name


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
