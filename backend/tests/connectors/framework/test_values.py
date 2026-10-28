from datetime import date

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    collapse,
    entity_kind_from,
    open_ended,
    parse_city_state_zip,
    parse_date,
    parse_npi,
    parse_npi_list,
    parse_state,
    parse_us_zip,
    split_identifiers,
)
from credentialing.domain.enums import EntityKind, ExclusionAction
from credentialing.domain.models import Address
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


@pytest.mark.parametrize("text", ["2019X345", "13/45/2019", "2014-03-01", "2014031", "201403011"])
def test_unreadable_dates_raise(text: str) -> None:
    with pytest.raises(ParseError):
        parse_date(text, ("%Y%m%d", "%m/%d/%Y"))


def test_spreadsheet_dates_and_integer_shaped_dates() -> None:
    formats = ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y")

    assert parse_date("2020-02-12", formats) == date(2020, 2, 12)
    assert parse_date("20200212", formats) == date(2020, 2, 12)
    assert parse_date("2/12/2020", formats) == date(2020, 2, 12)


@pytest.mark.parametrize(
    ("end_date", "expected"),
    [
        (date(2382, 12, 31), None),
        (date(2299, 5, 31), None),
        (date(2101, 1, 1), None),
        (date(2100, 12, 31), date(2100, 12, 31)),
        (date(2024, 1, 1), date(2024, 1, 1)),
        (None, None),
    ],
)
def test_open_ended_placeholders_are_no_end_date(
    end_date: date | None, expected: date | None
) -> None:
    assert open_ended(end_date) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("RN1, LPN2", ("RN1", "LPN2")),
        ("A1\nB2;C3", ("A1", "B2", "C3")),
        (" single ", ("single",)),
        ("", ()),
        (None, ()),
    ],
)
def test_split_identifiers(text: str | None, expected: tuple[str, ...]) -> None:
    assert split_identifiers(text) == expected


def test_parse_npi_list_keeps_valid_npis_in_order() -> None:
    first, second = make_npi("100000001"), make_npi("100000002")

    assert parse_npi_list(f"{first}\n{second}") == (first, second)
    assert parse_npi_list(f"{first}, {second}; {first}") == (first, second)
    assert parse_npi_list(f"{first}\nP1 - 1027986") == (first,)
    assert parse_npi_list("5203775500") == ()
    assert parse_npi_list(f"{first}0") == ()
    assert parse_npi_list("N/A") == ()
    assert parse_npi_list(None) == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Baltimore, MD 21201", Address(city="Baltimore", state="MD", postal_code="21201")),
        ("Reisterstown MD 21136", Address(city="Reisterstown", state="MD", postal_code="21136")),
        ("Indianapolis, IN", Address(city="Indianapolis", state="IN")),
        ("Phoenix AZ", Address(city="Phoenix", state="AZ")),
        ("Glen Burnie 21060", Address(city="Glen Burnie", postal_code="21060")),
        (
            "Sampleville, md. 21201-1234",
            Address(city="Sampleville", state="MD", postal_code="21201-1234"),
        ),
        ("Lanham MD 207060", Address(line1="Lanham MD 207060")),
        ("cics", Address(line1="cics")),
        ("MD 21201", Address(state="MD", postal_code="21201")),
        ("N/A", None),
        ("", None),
    ],
)
def test_parse_city_state_zip(text: str, expected: Address | None) -> None:
    assert parse_city_state_zip(text) == expected


def test_classify_action_takes_the_first_keyword_found() -> None:
    keywords = (
        ("exclu", ExclusionAction.EXCLUSION),
        ("suspen", ExclusionAction.SUSPENSION),
    )

    assert classify_action("OIG Exclusion", keywords) is ExclusionAction.EXCLUSION
    assert classify_action("License SUSPENDED", keywords) is ExclusionAction.SUSPENSION
    assert classify_action("Excluded and suspended", keywords) is ExclusionAction.EXCLUSION
    assert classify_action("License Revoked", keywords) is ExclusionAction.SANCTION
    assert classify_action(None, keywords) is ExclusionAction.SANCTION
    assert classify_action("", keywords, default=ExclusionAction.OTHER) is ExclusionAction.OTHER


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
