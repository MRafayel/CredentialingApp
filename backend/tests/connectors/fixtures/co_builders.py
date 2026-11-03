"""A synthetic Colorado HCPF terminations workbook in the shape read live on 2026-11-02.

One sheet, "Termination List", the header on row 1 with an unnamed empty sixth column, people
written "LAST, FIRST M" or "First Last", organizations by their business words, a
"Doing Business As" cell that is often a single space, "N/A" and several NPIs in one cell,
Excel dates, a row repeated with and without a "Doing Business As" name, a fully repeated
row and an empty row at the end. Every name and identifier is made up.
"""

from datetime import date

from tests.connectors.fixtures.builders import (
    INVALID_TEN_DIGITS,
    NPI_A,
    NPI_B,
    NPI_C,
    NPI_D,
    NPI_E,
    NPI_F,
    workbook,
)
from tests.synthetic import make_npi

NPI_G = make_npi("100000007")
NPI_H = make_npi("100000008")
NPI_I = make_npi("100000009")

HEADER = (
    "Provider Name",
    '"Doing Business As" Name',
    "NPI",
    "Termination Authority",
    "Termination Effective Date",
    None,
)
_E = "10 CCR 2505-10, Section 8.076.1.7.e"
_F = "10 C.C.R. 2505-10, Section 8.076.1.7.f"


def co_hcpf() -> bytes:
    """Colorado: 15 rows with data (one without a name), 12 records after two merges."""
    return workbook(
        [
            HEADER,
            ("Sampleton, Avery", " ", NPI_A, _E, date(2021, 3, 4)),
            ("MOCKWELL, JORDAN K", None, "N/A", _F, date(2015, 3, 3)),
            ("Riley Testperson", " ", NPI_B, _E, date(2023, 6, 1)),
            (
                "Sample Home Health Care, LLC",
                "Sample Care",
                f"{NPI_C} & {NPI_D}",
                "10 C.C.R. 2505-10, Section 8.076.1.7.b, 8.076.1.7.c",
                date(2020, 8, 11),
            ),
            ("Exampleton, Quinn  ", "Exampleton Wellness Clinic", NPI_E, _E, date(2019, 11, 20)),
            ("Demo, Casey", " ", NPI_F, _E, date(2022, 1, 4)),
            ("Demo, Casey", "Demo Therapy Services", NPI_F, _E, date(2022, 1, 4)),
            ("MOCKFORD, PAT", " ", NPI_G, _F, date(2018, 5, 10)),
            ("MOCKFORD, PAT", " ", NPI_G, _F, date(2018, 5, 10)),
            (
                "Mockville Pharmacy Inc",
                " ",
                f"{NPI_H}, {NPI_I}, {INVALID_TEN_DIGITS}",
                "42 CFR 455.416(c) ",
                date(2024, 1, 4),
            ),
            ("Jamie Q Sampleperson LPC", " ", int(NPI_A), _E, "6/15/2019"),
            ("TESTWOOD, MORGAN", " ", "N/A", _E, "pending review"),
            ("MOCKCARE TRANSPORT LLC (MOCKRIDE, TESTRIDE)", " ", "N/A", _F, date(2011, 1, 29)),
            ("Sampleford Family Dentistry", " ", NPI_B, _E, date(2026, 8, 3)),
            (None, " ", NPI_C, _E, date(2017, 2, 2)),
            (None, None, None, None, None),
        ],
        title="Termination List",
    )
