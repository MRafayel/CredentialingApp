"""A synthetic Delaware "Listing of Sanctioned Providers" PDF in the shape observed 2026-10-31.

Landscape pages of 1616 x 1249 pt, one ruled table per page with the header repeated, the
title above the grid, wrapped Comments cells, "N/A" placeholders, and every variant of the
name, licence and date columns the real file has. Every person, organization and identifier
is made up; the DEA numbers are not real registrations.
"""

from tests.connectors.fixtures.pdf_builders import build_table_pdf
from tests.synthetic import make_npi

NPI_A = make_npi("100000001")
NPI_B = make_npi("100000002")
NPI_C = make_npi("100000003")

DEA_NUMBERS = ("ZZ1234567", "ZY7654321")
"""Made-up DEA registration numbers the fixture prints; none may reach a record."""

HEADER = [
    "Year",
    "Sanctioned Provider Name",
    "NPI #",
    "Taxonomy #",
    "License #",
    "DEA #",
    "OIG / Medicaid\nSanction",
    "Effective Date",
    "Reinstated Date",
    "Comments",
]

COLUMN_WIDTHS = (40, 220, 90, 90, 130, 90, 330, 80, 170, 300)

PA_DHS = "Pennsylvania Department of Human Services"

PAGE_ONE = [
    HEADER,
    [
        "2025",
        "Avery J. Testperson, MD",
        NPI_A,
        "207Q00000X",
        "C1-0000001",
        DEA_NUMBERS[0],
        "Delaware Board of Medical Licensure and Discipline",
        "3/15/2025",
        "3/15/2030",
        "Suspended for three years\nwith conditions",
    ],
    [
        "2024",
        "Sampleville Health Care Inc",
        NPI_B,
        "",
        "N/A",
        "N/A",
        "OIG",
        "1/2/2024",
        "Indefinite",
        "",
    ],
    [
        "2026",
        "Jordan Sampleton (Employee)",
        "N/A",
        "",
        "RN000001L (PA)",
        "",
        PA_DHS,
        "6/1/2026",
        "",
        "",
    ],
    [
        "2016",
        "Riley Mockwell, LPN",
        "",
        "",
        "L1-0000002",
        "",
        "Before The Delaware Board of Nursing",
        "5/4/2014",
        "Reinstated effective 07/28/2015",
        "",
    ],
    [
        "2010",
        "Casey Example",
        "",
        "",
        "",
        "",
        "Medicaid Sanction",
        "12/2/2010",
        "6/1/2010",
        "",
    ],
]

PAGE_TWO = [
    HEADER,
    [
        "2014",
        "Jamie Q. Mockfield, PCA",
        "",
        "",
        "",
        "",
        "Pennsylvania Department of Human services",
        "7/20/014",
        "Revoked",
        "",
    ],
    [
        "2023",
        "Morgan Testwood RN, CRNP",
        NPI_C,
        "363LF0000X",
        "RN000003",
        DEA_NUMBERS[1],
        "OIG, Pennsylvania Department of Human Services",
        "2/3/2023",
        "2/3/2028",
        "",
    ],
    [
        "2026",
        "Jordan Sampleton (Employee)",
        "N/A",
        "",
        "RN000001L (PA)",
        "",
        PA_DHS,
        "6/1/2026",
        "6/1/2027",
        "Second listing",
    ],
    [
        "2025",
        "Taylor Sample-Person",
        "",
        "",
        "PROMISe#: 1234567",
        "",
        PA_DHS,
        "4/5/2025",
        "Indefinite",
        "",
    ],
    [
        "2013",
        "Mockname, Drew",
        "",
        "",
        "",
        "",
        "Delaware Secretary of State",
        "8/9/2013",
        "Annulled",
        "",
    ],
    [
        "2022",
        "Quinn Example-Doe, RN a.k.a. Quinn Doe",
        "",
        "",
        "R2-0000004",
        "",
        "Delaware Board of Nursing",
        "9/1/2022",
        "Active",
        "",
    ],
]


def de_dmap() -> bytes:
    """Two pages: 5 rows, then 6 more below a repeated header; one row repeats an identity."""
    return build_table_pdf(
        [PAGE_ONE, PAGE_TWO],
        title="Delaware Listing of Sanctioned Providers",
        page_size=(1616, 1249),
        column_widths=COLUMN_WIDTHS,
    )
