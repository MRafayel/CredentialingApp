import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.html_table import read_html_table, read_table
from credentialing.connectors.framework.rows import UnreadableRow

PAGE = b"""<!DOCTYPE html><html><head><meta charset="utf-8">
<script>document.write("<table><tr><td>not data</td></tr></table>");</script></head>
<body><p>Intro &amp; notes</p>
<table class="list">
  <thead><tr><th>Name</th><th colspan="2">Date</th></tr></thead>
  <tbody>
    <tr><td> Testperson,&#160;Avery </td><td>1/2/2020</td><td><b>State</b></td></tr>
    <tr><td>Mock &amp; Sample<br>Care</td><td>3/4/2021
      </td><td>O&#8217;Mock</td>
    <tr><td>Short row</td></tr>
    <tr><td></td><td>&nbsp;</td><td></td></tr>
    <tr><td>Nested <table><tr><td>inner</td></tr></table> cell</td><td>x</td><td>y</td></tr>
  </tbody>
</table>
<table><tr><td>Second</td><td>table</td></tr></table>
</body></html>"""


def test_reads_the_first_table_as_cell_texts() -> None:
    rows = read_table(PAGE)

    assert rows == [
        ["Name", "Date", ""],
        ["Testperson, Avery", "1/2/2020", "State"],
        ["Mock & Sample Care", "3/4/2021", "O\u2019Mock"],
        ["Short row"],
        ["", "", ""],
        ["Nested inner cell", "x", "y"],
    ]


def test_selects_another_table_by_index() -> None:
    assert read_table(PAGE, index=1) == [["Second", "table"]]


def test_a_missing_table_or_undecodable_body_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="no table 2"):
        read_table(PAGE, index=2)
    with pytest.raises(ParseError, match="no table 0"):
        read_table(b"<html><body>Service notice</body></html>")
    with pytest.raises(ParseError, match="not valid utf-8"):
        read_table(b"<table>\xff</table>")


def test_an_unclosed_table_keeps_its_last_row() -> None:
    assert read_table(b"<table><tr><td>A</td><td>B") == [["A", "B"]]


def test_rows_are_keyed_below_the_header_and_padded() -> None:
    rows = list(read_html_table(PAGE, header=("name", "DATE"), columns=("Name", "Date", "Kind")))

    assert rows == [
        {"Name": "Testperson, Avery", "Date": "1/2/2020", "Kind": "State"},
        {"Name": "Mock & Sample Care", "Date": "3/4/2021", "Kind": "O\u2019Mock"},
        {"Name": "Short row", "Date": "", "Kind": ""},
        {"Name": "Nested inner cell", "Date": "x", "Kind": "y"},
    ]


def test_a_row_wider_than_the_columns_is_unreadable() -> None:
    page = b"<table><tr><td>A</td></tr><tr><td>1</td><td>2</td><td></td></tr></table>"

    assert list(read_html_table(page, header=("A",))) == [
        UnreadableRow("table row 1 has 2 cells, expected 1")
    ]


def test_a_missing_header_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="no header row with Name, Status"):
        list(read_html_table(PAGE, header=("Name", "Status")))
