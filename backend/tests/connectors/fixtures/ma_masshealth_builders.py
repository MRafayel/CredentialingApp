"""Synthetic MassHealth workbook in the shape observed on 2026-11-02, with made-up rows.

Two sheets, the header on the first row of each: the providers currently suspended or
excluded, then the reinstated ones (whose "Provider Type " header ends in a space). Names are
one upper-case cell, first name first, with double spaces, comma suffixes and designators;
NPIs are numbers, text, "NONE" or empty; dates are Excel dates, with one typed as text.
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
    Rows,
    workbook,
)
from tests.synthetic import make_npi

NPI_G = make_npi("100000007")

LISTED_HEADER = (
    "Provider Name",
    "Provider Type",
    "National Provider Identifier (NPI)",
    "Unique ID",
    "Suspension/Exclusion Reason",
    "Suspension/Exclusion Effective Date",
)
REINSTATED_HEADER = (
    "Provider Name",
    "Provider Type ",
    "National Provider Identifier (NPI)",
    "Suspension/Exclusion Reason",
    "Suspension/Exclusion Effective Date",
    "Reinstated into MassHealth Effective Date",
    "Notes",
)

_BOARD = "DISCIPLINARY ACTION BY LICENSING BOARD"
_PROGRAM = "VIOLATION OF MASSHEALTH PROGRAM PARTICIPATORY REQUIREMENTS"
_REVOKED = "LICENSE REVOKED BY LICENSING BOARD"
_AFC = "ADULT FOSTER CARE / GROUP ADULT FOSTER CARE"

LISTED: Rows = [
    LISTED_HEADER,
    ("AVERY Q TESTPERSON", "PHYSICIAN", int(NPI_A), None, _BOARD, date(2019, 5, 1)),
    (
        "JORDAN  MOCKFIELD-SAMPLE",
        " PHYSICIAN",
        NPI_B,
        None,
        "MEDICARE/MEDICAID DECERTIFICAITON ",
        "03/15/2024",
    ),
    ("CASEY R SAMPLEWORTH", "PERSONAL CARE ATTENDANT", None, 123456, _PROGRAM, date(2023, 2, 9)),
    ("MORGAN T EXAMPLETON, II", "PHYSICIAN", "NONE", None, _REVOKED, date(2017, 8, 30)),
    (
        "SAMPLE HOME HEALTH SERVICES, LLC",
        "HOME HEALTH AGENCY",
        None,
        None,
        _PROGRAM,
        date(2021, 4, 2),
    ),
    ("RILEY MOCKWOOD", "GROUP PRACTICE ORGANIZATION", None, None, _PROGRAM, date(2020, 1, 6)),
    ("DREW K TESTWELL, MD PC", "GROUP PRACTICE ORGANIZATION", None, None, _BOARD, date(2018, 3, 3)),
    ("MOCKCO (SAMPLE HEALTH CARE SERVICES) LLC", _AFC, None, None, _PROGRAM, date(2022, 7, 7)),
    ("SAMPLECARE AGENCY INC", "SPECIAL PROGRAMS", int(NPI_C), None, _PROGRAM, date(2025, 6, 1)),
    ("SAMPLECARE AGENCY INC", "HOME HEALTH AGENCY", int(NPI_C), None, _PROGRAM, date(2025, 6, 1)),
    ("SAMPLECARE  AGENCY INC", _AFC, int(NPI_C), None, _PROGRAM, date(2025, 6, 1)),
    ("TAYLOR B MOCKSON", "NURSE PRACTITIONER", int(NPI_D), None, _BOARD, date(2016, 10, 11)),
    ("TAYLOR B MOCKSON", "PHYSICIAN", int(NPI_E), None, _BOARD, date(2016, 10, 11)),
    ("QUINN SAMPLEFORD", "DENTIST", int(INVALID_TEN_DIGITS), None, _REVOKED, "31/31/2024"),
    (None, "PHYSICIAN", None, None, _BOARD, date(2015, 1, 1)),
    ("TESTONYMOUS", "OPTICIAN", None, None, "CRIMINAL CONVICTION", date(2012, 12, 12)),
    (None, None, None, None, None, None),
]
"""16 data rows (one without a name, skipped) and a fully empty row at the end."""

REINSTATED: Rows = [
    REINSTATED_HEADER,
    ("ALEX  SAMPLEMAN", "PHYSICIAN", int(NPI_F), _BOARD, date(2018, 1, 2), date(2022, 6, 1), None),
    (
        "SAMPLE VISION CENTER, INC.",
        "GROUP PRACTICE ORGANIZATION",
        NPI_G,
        "MEDICARE SUSPENSION",
        date(2014, 5, 5),
        date(2016, 9, 1),
        "Reinstated after a made-up review",
    ),
]


def ma_masshealth(*, reinstated: Rows | None = REINSTATED) -> bytes:
    """The workbook; ``reinstated=None`` leaves out the second sheet."""
    if reinstated is None:
        return workbook(LISTED, title="Exclusions List")
    return workbook(
        reinstated, title="Removed fx the list", first_sheet=("Exclusions List", LISTED)
    )
