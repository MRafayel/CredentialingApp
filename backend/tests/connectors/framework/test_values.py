from datetime import date

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.values import (
    clean,
    collapse,
    entity_kind_from,
    parse_date,
    parse_npi,
    parse_state,
    parse_us_zip,
)
from credentialing.domain.enums import EntityKind
from tests.synthetic import make_npi


@pytest.mark.parametrize(
    ("text", "formats", "expected"),
    [
        ("20140301", ("%Y%m%d",), date(2014, 3, 1)),
        ("03/01/2014", ("%m/%d/%Y",), date(2014, 3, 1)),
        (" 03/01/2014 ", ("%Y%m%d", "%m/%d/%Y"), date(2014, 3, 1)),
        ("", ("%Y%m%d",), None),
        ("0", ("%Y%m%d",), None),
        ("00000000", ("%Y%m%d",), None),
        ("N/A", ("%m/%d/%Y",), None),
        (None, ("%m/%d/%Y",), None),
    ],
)
def test_parse_date(text: str | None, formats: tuple[str, ...], expected: date | None) -> None:
    assert parse_date(text, formats) == expected


@pytest.mark.parametrize("text", ["2019X345", "13/45/2019", "2014-03-01"])
def test_unreadable_dates_raise(text: str) -> None:
    with pytest.raises(ParseError):
        parse_date(text, ("%Y%m%d", "%m/%d/%Y"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  Sample   Clinic ", "Sample Clinic"),
        ("N/A", None),
        ("na", None),
        ("n/a", None),
        ("", None),
    ],
)
def test_clean_removes_placeholders(text: str, expected: str | None) -> None:
    assert clean(text) == expected


def test_collapse_keeps_placeholder_like_names() -> None:
    assert collapse("  NA ") == "NA"
    assert collapse("   ") is None


def test_parse_npi_accepts_only_valid_ten_digit_values() -> None:
    valid = make_npi("100000001")

    assert parse_npi(f" {valid} ") == valid
    assert parse_npi("123456789") is None
    assert parse_npi("0") is None
    assert parse_npi(valid[:-1] + str((int(valid[-1]) + 1) % 10)) is None
    assert parse_npi("") is None


def test_parse_state_and_zip() -> None:
    assert parse_state(" ny ") == "NY"
    assert parse_state("New York") is None
    assert parse_us_zip("2134") == "02134"
    assert parse_us_zip("10001") == "10001"
    assert parse_us_zip("100011234") == "10001-1234"
    assert parse_us_zip("0") is None
    assert parse_us_zip("K1A 0B1") == "K1A 0B1"


def test_entity_kind_from_name_columns() -> None:
    assert entity_kind_from("Sample Clinic LLC", None) is EntityKind.ORGANIZATION
    assert entity_kind_from(None, "Testperson") is EntityKind.INDIVIDUAL
    assert entity_kind_from(None, None) is EntityKind.UNKNOWN
