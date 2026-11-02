"""A synthetic West Virginia BMS exclusions PDF in the shape observed on 2026-10-31, made-up rows.

The real list is a 13-page landscape PDF written by Google Docs: a ruled grid with the header
on every page (later pages without the slash in the last column's name), wrapped names,
addresses and reasons. This builder keeps that shape on two pages with made-up people and
identifiers.
"""

from typing import Final

from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C, NPI_D, NPI_E, NPI_F
from tests.connectors.fixtures.pdf_builders import build_table_pdf

COLUMN_WIDTHS: Final = (72, 60, 62, 84, 60, 58, 28, 44, 48, 64, 140)

HEADER: Final = [
    "Last Name",
    "First Name",
    "NPI",
    "Address 1",
    "Address 2",
    "City",
    "State",
    "ZIP",
    "Action Date",
    "Excluded /\nTerminated",
    "Reason for Exclusion /\nTermination",
]
LATER_HEADER: Final = [*HEADER[:-1], "Reason for Exclusion\nTermination"]
"""Later pages spell the last column without its slash."""


def wv_bms() -> bytes:
    """Two pages: a person, a wrapped organization with a wrapped ZIP, an M/D/YY date in another
    state with an inmate number, every action spelling, a word broken over two lines, a
    professional corporation, a business named with a first name, three NPIs on one row."""
    first_page = [
        HEADER,
        [
            "Testperson",
            "Avery",
            NPI_A,
            "100 Sample Street",
            "",
            "Charleston",
            "WV",
            "25301",
            "1/20/2020",
            "Excluded\nIndefinitely",
            "OIG",
        ],
        [
            "Mockwell\nTesting\nLaboratories, LLC.",
            "",
            NPI_B,
            "200 Example\nAvenue",
            "Suite 100",
            "Morgantown",
            "WV",
            "26501-\n1234",
            "03/02/2026",
            "Terminated\nIndefinitely",
            "License revoked by WV\nBOM",
        ],
        [
            "Sampleton, Jr.",
            "Jordan\nLee",
            "N/A",
            "PO BOX 1000",
            "#12345-678",
            "Sampletown",
            "PA",
            "17837",
            "1/9/25",
            "Excluded",
            "Criminal Conviction",
        ],
        [
            "Mockman II",
            "Riley",
            NPI_C,
            "300 Mock Road",
            "Apt 2",
            "Wheeling",
            "WV",
            "26003\n-5678",
            "7/13/2022",
            "Excluded/\nTerminated",
            "Terminated by another\nState Medicaid Agency",
        ],
    ]
    second_page = [
        LATER_HEADER,
        [
            "Examplewood",
            "Maximilia\nnus",
            f"{NPI_D} -\nCMS\nDeactivated\nNPI",
            "400 Test Lane",
            "",
            "Columbus",
            "OH",
            "43004",
            "11/12/24",
            "Terminated/\nIndefinitely",
            "Medicare Revocation",
        ],
        [
            "Sampleperson MD\nInc",
            "Casey",
            NPI_E,
            "500 Clinic Way",
            "",
            "Huntington",
            "WV",
            "25701",
            "5/25/2022",
            "Terminated",
            "Medicare revocation",
        ],
        [
            "Mockwell &\nAssociates",
            "Dana",
            "N/A",
            "600 Office Park",
            "",
            "Beckley",
            "WV",
            "25801",
            "9/4/2020",
            "Terminated\nIndefinitely",
            "Terminated by 2 other\nState Medicaid\nAgencies",
        ],
        [
            "Example\nHealth Center",
            "",
            f"{NPI_F}\n{NPI_A}\n{NPI_B}",
            "700 Health Drive",
            "",
            "Parkersburg",
            "WV",
            "26101",
            "4/20/2011",
            "Excluded",
            "Owner Criminally\nConvicted",
        ],
    ]
    return build_table_pdf([first_page, second_page], column_widths=COLUMN_WIDTHS)
