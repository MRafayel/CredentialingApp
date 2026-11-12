"""How two licence numbers compare (owner, 2026-11-12): as written, by their digits once
padding or a part written on one side only is set aside, or the same digits with the parts
around them differing, which a person must decide."""

import pytest

from credentialing.domain.models import License
from credentialing.domain.policies import (
    MIN_LICENSE_DIGITS,
    LicenseMatch,
    compare_license_numbers,
    license_digits,
    same_license,
)

SAME = LicenseMatch.SAME
SAME_NUMBER = LicenseMatch.SAME_NUMBER
AFFIX_DIFFERS = LicenseMatch.AFFIX_DIFFERS
DIFFERENT = LicenseMatch.DIFFERENT


@pytest.mark.parametrize(
    ("number", "digits"),
    [
        ("00000551", "551"),
        ("PSY10213", "10213"),
        ("18-000869", "869"),  # the longest run, not the first
        ("036-123456", "123456"),
        ("12345-67890", "12345"),  # the first of two runs as long
        ("036123456", "36123456"),  # no punctuation, so one run
        ("ABC", ""),
        ("0000", ""),
        ("", ""),
    ],
)
def test_the_digits_of_a_licence(number: str, digits: str) -> None:
    assert license_digits(number) == digits


@pytest.mark.parametrize(
    ("own", "theirs", "expected"),
    [
        # As written, but for punctuation and case
        ("036123456", "036-123456", SAME),
        ("ab 123456", "AB-123.456", SAME),
        # Zero padding only, whatever the length (New York pads to eight digits)
        ("00000551", "000551", SAME_NUMBER),
        ("33087", "033087", SAME_NUMBER),
        ("A12", "A012", SAME_NUMBER),
        ("0001", "1", SAME_NUMBER),
        # A part written on one side only, with four or more digits
        ("10213", "PSY10213", SAME_NUMBER),
        ("10148", "10148T", SAME_NUMBER),
        ("060954", "D0060954", SAME_NUMBER),
        ("03260501 (PA DOH)", "03260501", SAME_NUMBER),
        ("036-123456", "123456", SAME_NUMBER),
        ("1234", "PSY1234", SAME_NUMBER),
        # Too few digits to stand alone against a part on one side
        ("123", "PSY123", DIFFERENT),
        ("00000367", "F000367", DIFFERENT),
        ("18-000869", "00000869", DIFFERENT),
        # Both sides write a part around the digits and they differ: a person decides
        ("RN123456", "LPN123456", AFFIX_DIFFERS),
        ("036-123456", "043-123456", AFFIX_DIFFERS),
        ("030138L", "RP030138L", AFFIX_DIFFERS),
        ("C01407", "COOO1407", AFFIX_DIFFERS),
        # Other numbers
        ("00000801", "F360431-1", DIFFERENT),
        ("01980501", "RN335717L", DIFFERENT),
        ("036123456", "123456", DIFFERENT),
        ("ABC", "ABC", SAME),
        ("ABC", "ABD", DIFFERENT),
        ("--", "12", DIFFERENT),
        ("", "", DIFFERENT),
    ],
)
def test_two_numbers_of_one_state(own: str, theirs: str, expected: LicenseMatch) -> None:
    assert compare_license_numbers(own, theirs) is expected
    assert compare_license_numbers(theirs, own) is expected


def test_the_floor_is_four_digits() -> None:
    assert MIN_LICENSE_DIGITS == 4
    assert compare_license_numbers("0999", "X999") is DIFFERENT
    assert compare_license_numbers("09999", "X9999") is SAME_NUMBER


def test_the_same_licence_needs_the_same_state_and_a_number_on_both_sides() -> None:
    assert same_license(License(number="00000551", state="NY"), License(number="551", state="NY"))
    assert not same_license(
        License(number="00000551", state="NY"), License(number="551", state="NJ")
    )
    assert not same_license(License(state="NY"), License(number="551", state="NY"))
    assert not same_license(
        License(number="RN123456", state="NY"), License(number="LPN123456", state="NY")
    )
