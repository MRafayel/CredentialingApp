import io

import pdfplumber
import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.rows import UnreadableRow
from tests.connectors.fixtures.pdf_builders import build_table_pdf

COLUMNS = ("Last Name", "First Name", "Exclusion Date", "Excluded / Terminated")
HEADER = ["Last Name", "First Name", "Exclusion Date", "Excluded / Terminated"]


def rows(body: bytes) -> list[dict[str, str] | UnreadableRow]:
    return list(read_pdf_table(body, expected_columns=COLUMNS))


def test_rows_below_the_header_are_keyed_by_the_expected_columns() -> None:
    body = build_table_pdf(
        [
            [
                ["Sampleville Medicaid Exclusion List", "", "", ""],
                ["LAST NAME", "First\nName", "Exclusion\nDate", "Excluded/ Terminated"],
                ["Testperson", "Avery", "01/02/2020", "Excluded"],
            ],
            [
                HEADER,
                ["Sampleton", "Jordan", "03/04/2021", "Terminated"],
                ["Excluded", "", "", ""],
            ],
        ],
        title="Made-up title drawn above the grid",
    )

    assert rows(body) == [
        {
            "Last Name": "Testperson",
            "First Name": "Avery",
            "Exclusion Date": "01/02/2020",
            "Excluded / Terminated": "Excluded",
        },
        {
            "Last Name": "Sampleton",
            "First Name": "Jordan",
            "Exclusion Date": "03/04/2021",
            "Excluded / Terminated": "Terminated",
        },
        {
            "Last Name": "Excluded",
            "First Name": "",
            "Exclusion Date": "",
            "Excluded / Terminated": "",
        },
    ]


def test_a_repeated_header_without_its_punctuation_is_still_skipped() -> None:
    body = build_table_pdf(
        [
            [HEADER, ["Testperson", "Avery", "01/02/2020", "Excluded"]],
            [
                ["Last Name", "First Name", "Exclusion Date", "Excluded\nTerminated"],
                ["Mockwell", "Riley", "05/06/2022", "Excluded"],
            ],
        ]
    )

    assert [row["Last Name"] for row in rows(body) if isinstance(row, dict)] == [
        "Testperson",
        "Mockwell",
    ]


def test_wrapped_cells_keep_their_line_breaks_and_blank_rows_are_skipped() -> None:
    npis = "1000000017\n1000000025"
    body = build_table_pdf(
        [
            [
                HEADER,
                ["  Testperson  ", "", "01/02/2020", "Excluded\nPending (review)"],
                ["", "", "", ""],
                ["Sample Care\\Group", "Avery", npis, ""],
            ]
        ]
    )

    assert rows(body) == [
        {
            "Last Name": "Testperson",
            "First Name": "",
            "Exclusion Date": "01/02/2020",
            "Excluded / Terminated": "Excluded\nPending (review)",
        },
        {
            "Last Name": "Sample Care\\Group",
            "First Name": "Avery",
            "Exclusion Date": npis,
            "Excluded / Terminated": "",
        },
    ]


def test_a_cell_without_its_left_rule_keeps_its_text() -> None:
    pages = [
        [
            HEADER,
            ["Testperson", "Avery", "01/02/2020", "Excluded"],
            ["Sampleton", "Jordan", "03/04/2021", "Terminated"],
            ["Mockwell", "Riley", "05/06/2022", "Excluded"],
        ]
    ]
    body = build_table_pdf(pages, open_left_rows={(0, 2)})
    with pdfplumber.open(io.BytesIO(body)) as document:
        assert document.pages[0].extract_tables()[0][2][0] is None

    assert [row["Last Name"] for row in rows(body) if isinstance(row, dict)] == [
        "Testperson",
        "Sampleton",
        "Mockwell",
    ]


def test_columns_covered_by_a_merged_cell_stay_empty() -> None:
    note = "Sampleville note running across more than one column of the grid"
    body = build_table_pdf(
        [[HEADER, [note], ["Testperson", "", "", "x"]]], column_widths=[720, 180, 180, 180]
    )
    with pdfplumber.open(io.BytesIO(body)) as document:
        assert document.pages[0].extract_tables()[0][1] == [note, None, None, None]

    assert rows(body) == [
        {"Last Name": note, "First Name": "", "Exclusion Date": "", "Excluded / Terminated": ""},
        {
            "Last Name": "Testperson",
            "First Name": "",
            "Exclusion Date": "",
            "Excluded / Terminated": "x",
        },
    ]


def test_a_table_with_another_column_count_yields_unreadable_rows() -> None:
    body = build_table_pdf(
        [
            [HEADER, ["Testperson", "Avery", "01/02/2020", "Excluded"]],
            [["Sampleton", "Jordan", "03/04/2021"]],
        ]
    )

    assert rows(body)[1:] == [UnreadableRow("3 cells instead of 4")]


def test_rows_before_the_first_header_are_skipped_whatever_their_width() -> None:
    body = build_table_pdf(
        [
            [["Sampleville notice"]],
            [HEADER, ["Testperson", "Avery", "01/02/2020", "Excluded"]],
        ]
    )

    assert len(rows(body)) == 1


def test_a_document_without_the_header_is_a_parse_error() -> None:
    body = build_table_pdf([[["Name", "Date"], ["Testperson", "01/02/2020"]]])

    with pytest.raises(ParseError, match="no header row with Last Name, First Name") as error:
        rows(body)
    assert "Testperson" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not a pdf", id="not-a-pdf"),
        pytest.param(b"", id="empty"),
        pytest.param(
            build_table_pdf([[HEADER, *[["Testperson", "Avery", "01/02/2020", "x"]] * 30]])[:2000],
            id="truncated",
        ),
    ],
)
def test_an_unreadable_body_is_a_parse_error(body: bytes) -> None:
    with pytest.raises(ParseError, match="not a readable PDF"):
        rows(body)


def test_a_document_without_pages_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="no pages"):
        rows(build_table_pdf([]))


def test_the_builder_draws_one_table_per_page_that_pdfplumber_reads_back() -> None:
    pages = [
        [HEADER, ["Testperson", "Avery (dba)", "Sample\nCare", ""]],
        [["Mockwell", "Riley"], ["Sampleton", "Jordan\nLee"]],
    ]

    with pdfplumber.open(io.BytesIO(build_table_pdf(pages, title="Made-up title"))) as document:
        tables = [page.extract_tables() for page in document.pages]

    assert tables == [[[list(row) for row in page]] for page in pages]


def test_the_builder_rejects_rows_that_do_not_fit_a_page() -> None:
    with pytest.raises(ValueError, match="do not fit"):
        build_table_pdf([[["Testperson\nAvery"]] * 30])
    with pytest.raises(ValueError, match="more cells than column_widths"):
        build_table_pdf([[["Testperson", "Avery"]]], column_widths=[100])
