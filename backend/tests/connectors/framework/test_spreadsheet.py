import warnings
import zipfile
from datetime import date
from io import BytesIO

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


# --- Power Pages exports: an empty fill in the stylesheet (Illinois, 2026-11-03) ------------

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_EXPORT_STYLES = (
    f'\ufeff<?xml version="1.0" encoding="utf-8"?><x:styleSheet xmlns:x="{_MAIN}">'
    "<x:numFmts /><x:fonts><x:font /></x:fonts><x:fills><x:fill /></x:fills>"
    "<x:borders><x:border /></x:borders><x:cellStyleXfs><x:xf /></x:cellStyleXfs>"
    '<x:cellXfs><x:xf /><x:xf numFmtId="14" applyNumberFormat="1" /></x:cellXfs>'
    "</x:styleSheet>"
)
"""The stylesheet as the export writes it: every element empty, dates as format 14."""


def _export_workbook(styles: str = _EXPORT_STYLES) -> bytes:
    """A workbook written by hand in the export's shape: prefixed elements, inline strings
    and a date serial (2025-06-18) in a cell styled as a date."""
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.'
            'openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="{_RELATIONSHIPS}">'
            f'<Relationship Type="{_DOCUMENT}/officeDocument" Target="/xl/workbook.xml" '
            'Id="R1" /></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="utf-8"?><x:workbook xmlns:x="{_MAIN}"><x:sheets>'
            f'<x:sheet name="Active Sanctions" sheetId="1" r:id="R3" xmlns:r="{_DOCUMENT}" />'
            "</x:sheets></x:workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="{_RELATIONSHIPS}">'
            f'<Relationship Type="{_DOCUMENT}/styles" Target="/xl/styles.xml" Id="R2" />'
            f'<Relationship Type="{_DOCUMENT}/worksheet" Target="/xl/worksheets/sheet.xml" '
            'Id="R3" /></Relationships>'
        ),
        "xl/styles.xml": styles,
        "xl/worksheets/sheet.xml": (
            f'<?xml version="1.0" encoding="utf-8"?><x:worksheet xmlns:x="{_MAIN}">'
            '<x:sheetData><x:row r="1"><x:c r="A1" t="inlineStr"><x:is><x:t>Name</x:t>'
            '</x:is></x:c><x:c r="B1" t="inlineStr"><x:is><x:t>Date</x:t></x:is></x:c>'
            '</x:row><x:row r="2"><x:c r="A2" t="inlineStr"><x:is><x:t>Testperson Avery'
            '</x:t></x:is></x:c><x:c r="B2" s="1" t="n"><x:v>45826</x:v></x:c></x:row>'
            "</x:sheetData></x:worksheet>"
        ),
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return output.getvalue()


def test_a_workbook_with_an_empty_fill_is_read_after_repair() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # openpyxl's "no default style" warning is silenced

        rows = list(read_sheet(_export_workbook(), expected_columns=("Name", "Date")))

    assert rows == [{"Name": "Testperson Avery", "Date": "2025-06-18"}]


def test_an_unprefixed_empty_fill_is_repaired_too() -> None:
    styles = _EXPORT_STYLES.replace("x:", "").replace(f' xmlns:x="{_MAIN}"', f' xmlns="{_MAIN}"')
    styles = styles.replace("<fill />", "<fill/>")

    rows = list(read_sheet(_export_workbook(styles), expected_columns=("Name", "Date")))

    assert rows == [{"Name": "Testperson Avery", "Date": "2025-06-18"}]


@pytest.mark.parametrize(
    "styles",
    [
        _EXPORT_STYLES.replace("<x:numFmts />", "<x:numFmts><x:numFmt /></x:numFmts>"),
        _EXPORT_STYLES.replace("<x:numFmts />", "<x:numFmts><x:numFmt /></x:numFmts>").replace(
            "<x:fill />", '<x:fill><x:patternFill patternType="none"/></x:fill>'
        ),
    ],
    ids=["still-broken-after-repair", "nothing-to-repair"],
)
def test_another_stylesheet_type_error_is_a_parse_error(styles: str) -> None:
    with pytest.raises(ParseError, match=r"not a readable xlsx workbook \(TypeError\)"):
        list(read_sheet(_export_workbook(styles), expected_columns=("Name", "Date")))
