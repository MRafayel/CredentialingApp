from datetime import date

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.spreadsheet import read_sheet
from tests.connectors.fixtures.builders import Rows, workbook

COLUMNS = ("Name", "NPI", "Date")


def test_the_header_is_found_below_title_rows() -> None:
    body = workbook(
        [
            ("Some List - October 2026",),
            (),
            ("Name", "NPI", "Date"),
            ("Testperson, Avery", 1000000012, date(2024, 3, 1)),
        ]
    )

    assert list(read_sheet(body, expected_columns=COLUMNS)) == [
        {"Name": "Testperson, Avery", "NPI": "1000000012", "Date": "2024-03-01"}
    ]


def test_header_names_compare_without_case_padding_or_line_breaks() -> None:
    body = workbook([("FULL\nNAME ", "npi\xa0", "  Date"), ("Avery", "1", "x")])

    assert list(read_sheet(body, expected_columns=("Full Name", "NPI", "Date"))) == [
        {"Full Name": "Avery", "NPI": "1", "Date": "x"}
    ]


def test_a_repeated_header_name_is_numbered() -> None:
    body = workbook([("Name", "State", "State"), ("Avery", "VT", "22")])

    rows = read_sheet(body, expected_columns=("Name", "State", "State (2)"))

    assert list(rows) == [{"Name": "Avery", "State": "VT", "State (2)": "22"}]


def test_cells_become_text() -> None:
    body = workbook(
        [
            ("A", "B", "C", "D", "E", "F", "G"),
            (date(2020, 2, 12), 1234567890.0, 2.5, "  padded  ", None, True, "07"),
        ]
    )

    (row,) = read_sheet(body, expected_columns=("A", "B", "C", "D", "E", "F", "G"))

    assert row == {
        "A": "2020-02-12",
        "B": "1234567890",
        "C": "2.5",
        "D": "padded",
        "E": "",
        "F": "TRUE",
        "G": "07",
    }


def test_text_dates_stay_as_written() -> None:
    body = workbook([("Name", "NPI", "Date"), ("Avery", "N/A", "03/15/2021")])

    (row,) = read_sheet(body, expected_columns=COLUMNS)

    assert row["Date"] == "03/15/2021"
    assert row["NPI"] == "N/A"


def test_empty_rows_are_skipped_and_short_rows_padded() -> None:
    body = workbook([("Name", "NPI", "Date"), ("Avery",), (), (None, None, None), ("Jordan", 2)])

    assert list(read_sheet(body, expected_columns=COLUMNS)) == [
        {"Name": "Avery", "NPI": "", "Date": ""},
        {"Name": "Jordan", "NPI": "2", "Date": ""},
    ]


def test_stop_at_blank_row_leaves_out_footnotes() -> None:
    body = workbook([("Name", "NPI", "Date"), ("Avery", 1, None), (), ("Key: footnote",)])

    rows = list(read_sheet(body, expected_columns=COLUMNS, stop_at_blank_row=True))

    assert [row["Name"] for row in rows] == ["Avery"]


def test_cells_beyond_the_header_are_ignored() -> None:
    body = workbook([("Name", "NPI", "Date", None, None), ("Avery", 1, None, None, "note")])

    assert list(read_sheet(body, expected_columns=COLUMNS)) == [
        {"Name": "Avery", "NPI": "1", "Date": ""}
    ]


def test_the_first_sheet_is_read_by_default_and_others_by_name_or_index() -> None:
    first: tuple[str, Rows] = ("SEPTEMBER 2026", [("Name", "NPI", "Date"), ("First", 1, None)])
    body = workbook([("Name", "NPI", "Date"), ("Second", 2, None)], title="Data", first_sheet=first)

    assert [r["Name"] for r in read_sheet(body, expected_columns=COLUMNS)] == ["First"]
    assert [r["Name"] for r in read_sheet(body, sheet=1, expected_columns=COLUMNS)] == ["Second"]
    assert [r["Name"] for r in read_sheet(body, sheet="Data", expected_columns=COLUMNS)] == [
        "Second"
    ]


@pytest.mark.parametrize("sheet", [2, "Missing"])
def test_a_missing_sheet_is_a_parse_error(sheet: int | str) -> None:
    body = workbook([("Name", "NPI", "Date")], first_sheet=("Other", [()]))

    with pytest.raises(ParseError, match="sheet"):
        list(read_sheet(body, sheet=sheet, expected_columns=COLUMNS))


def test_a_header_outside_the_search_rows_is_a_parse_error() -> None:
    body = workbook([("Title",), (), ("Name", "NPI", "Date"), ("Avery", 1, None)])

    with pytest.raises(ParseError, match="no header row"):
        list(read_sheet(body, expected_columns=COLUMNS, header_search_rows=2))


def test_a_changed_header_is_a_parse_error() -> None:
    body = workbook([("Name", "NPI", "Exclusion Date"), ("Avery", 1, None)])

    with pytest.raises(ParseError, match="no header row"):
        list(read_sheet(body, expected_columns=COLUMNS))


@pytest.mark.parametrize("body", [b"", b"<html><body>Service notice</body></html>", b"PK\x03\x04"])
def test_a_body_that_is_not_a_workbook_is_a_parse_error(body: bytes) -> None:
    with pytest.raises(ParseError, match="not a readable xlsx workbook"):
        list(read_sheet(body, expected_columns=COLUMNS))
