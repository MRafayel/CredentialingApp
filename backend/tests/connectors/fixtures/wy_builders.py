"""A synthetic Wyoming Medicaid exclusion PDF in the shape observed on 2026-10-31, made-up rows.

The real list is a 6-page landscape PDF exported from Excel: a title row inside the grid, the
header once on page 1, free-text provider numbers, former names in parentheses and an empty
row at the end. This builder keeps that shape on two pages with made-up people, businesses
and identifiers.
"""

from typing import Final

from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C
from tests.connectors.fixtures.pdf_builders import build_table_pdf

COLUMN_WIDTHS: Final = (92, 98, 112, 110, 87, 71, 54, 63)

TITLE_ROW: Final = ["Wyoming Medicaid Provider Exclusion List", "", "", "", "", "", "", ""]
HEADER: Final = [
    "Last Name",
    "First Name",
    "Business Name",
    "Provider Type",
    "Provider Number",
    "City",
    "State",
    "Exclusion\nDate",
]
EMPTY_ROW: Final = ["", "", "", "", "", "", "", ""]


def wy_wdh() -> bytes:
    """Two pages: an organization, a former name in each position, "a.k.a" names, marked and
    unmarked business names, the Provider Number patterns, a duplicate pair, a person whose
    last name the grid lost, and the empty last row."""
    first_page = [
        TITLE_ROW,
        HEADER,
        row("", "", "Samplewood's Home Care", "Case Management\nWaiver", "120000100", "Casper"),
        row("Tester (Maidmock)", "Avery Quinn", "", "Cert. Nursing Assist.", "20001"),
        row("(Formermock) Sampleton", "Jordan", "", "Registered Nurse", "RN #12345"),
        row("Mockwell(Priorname)", "Riley Dawn", "", "Licensed Practical\nNurse", "LPN5001"),
        row(
            "Exampleton",
            "Bartholomew a.k.a Bart",
            "Exampleton Mock\nVentures",
            "Training DD Waiver",
            "127000700",
            "Laramie",
        ),
        row(
            "Exampleton",
            "Bartholomew a.k.a Bart",
            "Sampleville Support\nCenter (SSC)",
            "Training DD Waiver",
            "127000700",
            "Laramie",
        ),
        row(
            "Testwell",
            "Morgan",
            "aka-Priorwell, Morgan E",
            "Pharmacy Technician",
            "Lic# 1234567-\n1234(UT)",
            "Ogden",
            "UT",
        ),
        row(
            "Mockridge",
            "Taylor Rae",
            "Sample Breathing Supply",
            "DME Provider",
            f"NPI {NPI_A};\nWY Provider ID\n1234567 00",
        ),
        row(
            "Nursemock",
            "Pat",
            "",
            "Registered Nurse",
            "RN 31001 (WY)\nRN 012345678\n(OR)\nRN 100001 (AZ)",
            "Denver",
            "CO",
        ),
    ]
    second_page = [
        row("Twomock", "Sam", "", "Nurse/Nurse Assistant", "WY20002,\nRN123456 CO"),
        row(
            "Aidemock",
            "Lee",
            "",
            "Certified Nursing\nAssistant/WY License #\n20003",
            "N/A",
        ),
        row(
            "Doctormock",
            "Jamie",
            "Example Pines Clinic",
            "General Practice",
            "111000002\n111000003",
        ),
        row(
            "Ownermock",
            "Alex",
            "Alex Ownermock Nursing\nServices, Inc.",
            "Nurse/Nurse Assistant",
            f"{NPI_B}NPI",
        ),
        row("Suffixmock JR", "Lee Allen", "", "Medical Doctor", f"NPI: {NPI_C}"),
        row("Quinnmock", "Dana Lynn\n(Oldname, Othername)", "", "Waiver", "N/A"),
        row("Altmock", "Casey Jo\n(aka Casey Formerton)", "", "Waiver", "N/A"),
        row("Listmock", "Robin Kay", "aka: Onemock, Twomock,\nThreemock", "C.N.A.", "CNA3001"),
        row("", "Skyler Mae", "", "Cert. Nursing Assist.", "20004"),
        EMPTY_ROW,
    ]
    return build_table_pdf([first_page, second_page], column_widths=COLUMN_WIDTHS)


def row(
    last: str,
    first: str,
    business: str,
    provider_type: str,
    number: str,
    city: str = "Cheyenne",
    state: str = "WY",
    date: str = "6/23/2022",
) -> list[str]:
    """One table row in the file's column order."""
    return [last, first, business, provider_type, number, city, state, date]
