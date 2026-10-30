"""Synthetic Minnesota workbooks in the shape the survey recorded (2026-10-19), made-up rows.

The real files could not be re-read on 2026-10-30 (the bot manager redirected every request),
so the layout follows the survey: the header on the first row, dates typed as text.
"""

from datetime import date

from tests.connectors.fixtures.builders import workbook

INDIVIDUAL_COLUMNS = (
    "Provider type Description",
    "Last name",
    "First name",
    "Middle name",
    "Effective date of exclusion",
    "Address line1",
    "Address line2",
    "City",
    "State",
    "Zip code",
    "Exclusion status",
)

ORGANIZATION_COLUMNS = (
    "Provider type description",
    "Practice name",
    "Effective date of exclusion",
    "Address line1",
    "Address line2",
    "City",
    "State",
    "Zip code",
    "Exclusion status",
)


def mn_dhs_individuals() -> bytes:
    """Individuals: mostly Direct Support Workers without an address; one bad date."""
    return workbook(
        [
            INDIVIDUAL_COLUMNS,
            (
                "Direct Support Worker",
                "Testperson",
                "Avery",
                "J",
                "3/15/2024",
                None,
                None,
                None,
                None,
                None,
                "Medicaid authority",
            ),
            (
                "Physician",
                "Sampleton ",
                "Jordan",
                None,
                date(2019, 1, 7),
                "100 Example Ave",
                "Suite 2",
                "Sampleville",
                "mn",
                55101,
                "Medicare Medicaid exclusion",
            ),
            (
                "Direct Support Worker",
                "Mockfield",
                None,
                None,
                "13/45/2024",
                None,
                None,
                None,
                None,
                None,
                "Denied not eligible",
            ),
            (
                "Direct Support Worker",
                None,
                "Riley",
                None,
                "1/2/2025",
                None,
                None,
                None,
                None,
                None,
                "Medicaid authority",
            ),
        ]
    )


def mn_dhs_organizations() -> bytes:
    """Organizations: upper-case statuses and an address on every row."""
    return workbook(
        [
            ORGANIZATION_COLUMNS,
            (
                "Personal Care Provider Organization",
                "Sample Home Care LLC",
                "6/1/2023",
                "200 Mock St",
                None,
                "Testburg",
                "MN",
                "55401-1234",
                "MEDICAID AUTHORITY",
            ),
            (
                "Pharmacy",
                "Exampleville Drug",
                date(2021, 11, 30),
                "9 Fake Rd",
                "Unit B",
                "Exampleville",
                "WI",
                "54601",
                "MEDICARE MEDICAID EXCLUSION",
            ),
            (
                "Adult Day Care",
                "   ",
                "2/2/2022",
                "1 Nowhere Ln",
                None,
                "Testburg",
                "MN",
                "55401",
                "DENIED NOT ELIGIBLE",
            ),
        ]
    )
