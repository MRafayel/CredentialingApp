"""A synthetic Nevada OMIG exclusions PDF in the shape observed on 2026-10-31, made-up rows.

The real list is a 36-page landscape PDF (1008 x 612 pt) with a ruled grid and a three-line
header repeated on every page. This builder keeps that shape on two pages, so the header
repeat is exercised, with made-up people, entities and identifiers.
"""

from typing import Final

from tests.connectors.fixtures.builders import INVALID_TEN_DIGITS, NPI_A, NPI_B, NPI_C
from tests.connectors.fixtures.pdf_builders import build_table_pdf

MEDICAID_ID_A: Final = "9005001234"
MEDICAID_ID_B: Final = "9005005678"
"""Ten-digit Nevada Medicaid provider IDs as the NPI column carries them."""

PAGE_SIZE: Final = (1008.0, 612.0)
COLUMN_WIDTHS: Final = (150, 62, 45, 45, 120, 110, 62, 50, 82, 70, 60, 60)

HEADER: Final = [
    "Excluded\nProviders/Entities\nand/or Individuals",
    "Sanctioned/\nExcluded NPI",
    "Medicaid\nProvider",
    "Provider\nType",
    "Associated Legal\nEntity",
    "Persons with\ncontrolling interest\nof 5% or more",
    "Contract\nTermination\nDate",
    "Nevada\nMedicaid\nSanction Tier",
    "Nevada Medicaid\nSanction Period",
    "Nevada Medicaid\nSanction Period\nEnd Date",
    "OIG Exclusion\nDate",
    "OIG Reinstate\nDate",
]
"""The header as the real file wraps it: matched on letters and digits only."""

ENTITY: Final = "Sunny Mesa Wellness"
"""An entity name with no business word: only another row's legal-entity cell says so."""


def nv_omig() -> bytes:
    """Two pages: federal and tier rows, an entity with its controlling persons, notes."""
    first_page = [
        HEADER,
        [
            "Avery Q. Testperson MD",
            NPI_A,
            "X",
            "20",
            "",
            "",
            "3/14/2019",
            "Federal",
            "Permanent",
            "",
            "2/20/2019",
            "5/1/2024",
        ],
        _row("Jordan Sampleton", "", "", "", "", "", "6/30/2015", "Federal", "Permanent"),
        [
            ENTITY,
            f"{NPI_B}\n{MEDICAID_ID_A}",
            "X",
            "14",
            "",
            "Riley Mockwell",
            "1/15/2021",
            "2",
            "10 Years",
            "1/15/2031",
            "",
            "",
        ],
        [
            ENTITY,
            f"{NPI_B}\n{MEDICAID_ID_A}",
            "X",
            "14",
            "",
            "Casey Fakename",
            "1/15/2021",
            "2",
            "10 Years",
            "1/15/2031",
            "",
            "",
        ],
        _row(
            "Riley Mockwell",
            "",
            "",
            "",
            ENTITY,
            "Casey Fakename",
            "1/15/2021",
            "2",
            "10 Years",
            "1/15/2031",
        ),
        _row(
            "Morgan Exampleton aka Morgan\nOtherton",
            MEDICAID_ID_B,
            "X",
            "30, 48",
            "",
            "",
            "11/2/2022",
            "3",
            "3 years",
            "11/2/2025",
        ),
        [
            "Sample Care Services LLC\ndba Sample Treatment Center",
            INVALID_TEN_DIGITS,
            "X",
            "58",
            "",
            "Pat Testowner\n& Lee Mockpartner",
            "4/20/2016\n11/23/2016",
            "1",
            "Permanent",
            "4/20/2026",
            "",
            "",
        ],
    ]
    second_page = [
        HEADER,
        [
            "Taylor Fakerson",
            NPI_C,
            "",
            "",
            "",
            "",
            "6/30/2026",
            "2\n3",
            "10 Years\n3 Years\n* imposed a 10\nyear sanction",
            "6/30/2036",
            "",
            "",
        ],
        _row("Drew Q Mockford", "", "", "", "", "", "7/14/2021", "4", "12 Months", "7/14/2022*"),
        _row("Jamie Testwell", "", "", "", "", "", "", "", "", "", "9/9/2019"),
        _row("Quinn Placeholder"),
        _row("As of 8/1/2017 all column titles\nhave been updated"),
    ]
    return build_table_pdf(
        [first_page, second_page], page_size=PAGE_SIZE, column_widths=COLUMN_WIDTHS
    )


def _row(*cells: str) -> list[str]:
    """A row with its trailing empty cells filled in."""
    return [*cells, *[""] * (len(HEADER) - len(cells))]
