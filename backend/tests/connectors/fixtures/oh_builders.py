"""Synthetic Ohio Medicaid workbook in the live shape of 2026-11-02, with made-up rows.

Three sheets as published: an empty "Instructions" sheet, "Individuals" and "Organizations",
each with its header on the first row; dates as Excel dates and a few typed as text, NPIs and
provider numbers as numbers or text lists, empty rows below the people, a ZIP
cell formatted as a date and one organization listed twice under two provider types.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from io import BytesIO

from openpyxl import Workbook

from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C, NPI_D, NPI_E, Cell

INDIVIDUAL_COLUMNS = (
    "Last Name",
    "First Name",
    "Middle Name",
    "DOB",
    "NPI",
    "Provider ID",
    "Status",
    "Action Date",
    "Date Added",
    "Provider Type",
)

ORGANIZATION_COLUMNS = (
    "NPI",
    "Organization Name",
    "Address 1",
    "Address 2",
    "City",
    "State",
    "Zip Code",
    "Provider ID",
    "Status",
    "Action Date",
    "Date Added",
    "Provider Type",
)

ZIP_AS_DATE = date(1899, 12, 30) + timedelta(days=43215)
"""The ZIP 43215 in a cell formatted as a date, as Excel shows it."""

INDIVIDUALS: Sequence[Sequence[Cell]] = (
    INDIVIDUAL_COLUMNS,
    (
        "TESTPERSON",
        "AVERY",
        "QUINN",
        date(1971, 2, 3),
        int(NPI_A),
        "0123456",
        "Excluded",
        date(2024, 5, 6),
        date(2024, 6, 1),
        "38 - Certified Nurse Practitioner",
    ),
    ("MOCKWELL (PRIORNAME) ", "RILEY", "DAWN", date(1980, 7, 8), " ", None, "Excluded",
     date(2019, 3, 4), None, None),
    ("SAMPLETON aka FORMERTON", "JORDAN", None, date(1975, 1, 2), None, 2345678, "Excluded",
     date(2018, 9, 10), None, None),
    ("EXAMPLEDOC, Jr.", "MORGAN", "LEE", date(1966, 4, 5), None, None, "Suspended",
     date(2025, 1, 15), None, "20 - Physician/Osteopath"),
    ("NURSEMOCK JR", "CASEY", None, date(1990, 11, 12), None, None, "Excluded",
     date(2021, 2, 3), None, None),
    ("NICKMOCK", "ALEXANDRA “ALEX”", None, date(1985, 6, 7), None, None, "Excluded",
     date(2022, 8, 9), None, None),
    ("INITIALMOCK", "JAMIE R.", None, date(1979, 10, 11), None, None, "Excluded",
     date(2020, 12, 1), None, None),
    (
        "LISTMOCK",
        "DANA",
        "KAY",
        date(1968, 3, 30),
        f"{NPI_B}, {NPI_C}, 123456",
        "123456, 3456789",
        "Excluded",
        date(2017, 4, 5),
        "Updated - 1/2/25",
        "38 - Certified Nurse Practitioner, 72 - Pharmacist",
    ),
    ("TEXTDATEMOCK", "ROBIN", None, "3/4/1970", None, "REG ID 123456", "suspended",
     "5/6/2020\xa0\xa0\xa0 ", None, None),
    ("NOSTATUSMOCK", "SKYLER", None, date(1992, 1, 1), None, None, None, date(2023, 3, 3),
     None, None),
    ("BADDATEMOCK", "TAYLOR", None, None, None, None, "Excluded", "13/45/2020", None, None),
    (None, "NOLASTNAME", None, date(1990, 1, 1), None, None, "Excluded", date(2020, 1, 1),
     None, None),
    (None,) * 10,
    (None,) * 10,
)  # fmt: skip

ORGANIZATIONS: Sequence[Sequence[Cell]] = (
    ORGANIZATION_COLUMNS,
    (
        int(NPI_D),
        "SAMPLE CARE SERVICES, LLC dba SAMPLE HOME HEALTH",
        "100 Example Rd",
        "Suite 200",
        "Columbus",
        "OH",
        43004,
        "0234567",
        "Excluded",
        date(2023, 7, 1),
        date(2023, 8, 1),
        "60 - Medicare Certified Home Health Agency",
    ),
    (
        int(NPI_D),
        "SAMPLE CARE SERVICES, LLC dba SAMPLE HOME HEALTH",
        "100 Example Rd",
        "Suite 200",
        "Columbus",
        "OH",
        43004,
        "0234567",
        "Excluded",
        date(2023, 7, 1),
        date(2023, 8, 1),
        "45 - WAIVERED SERVICES ORGANIZATION",
    ),
    (NPI_E, "Mockridge Wellness Center (MWC)", "9 Sample St", None, "Kansas City", "MO",
     "64101", 345678, "Susepnded", date(2025, 2, 2), None, "21 - Professional Medical Group"),
    (None, "SAMPLEPERSON AVERY DO LLC dba MOCK FAMILY CARE", "5 Mock Ave", None, "Dayton",
     "OH", ZIP_AS_DATE, "4567890", "Terminated", None, None, "21 - Professional Medical Group"),
)  # fmt: skip


def oh_medicaid() -> bytes:
    """The workbook: "Instructions", "Individuals" (13 rows, then empty rows)
    and "Organizations" (4 rows, one repeated under another provider type)."""
    book = Workbook()
    instructions = book.active
    instructions.title = "Instructions"
    for title, rows in (("Individuals", INDIVIDUALS), ("Organizations", ORGANIZATIONS)):
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
