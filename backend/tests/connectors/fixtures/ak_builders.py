"""A synthetic Alaska excluded provider list in the shape observed on 2026-11-04, made-up rows.

The real list is a 12-page landscape PDF exported from Excel: a letterhead page without a
table, then one ruled table per page with the header repeated, organizations written with "-"
as their first name, other names in parentheses, a reinstated party marked "*", a few rows
drawn without their inner rules and a footer note. This builder keeps that shape on three
pages with made-up people and businesses, next to a landing page with the one document link.
"""

from typing import Final

from tests.connectors.fixtures.pdf_builders import build_table_pdf

COLUMN_WIDTHS: Final = (95, 144, 110, 128, 76, 125)

HEADER: Final = [
    "EXCLUSION DATE",
    "LAST NAME",
    "FIRST NAME",
    "PROVIDER TYPE",
    "EXCLUSION\nAUTHORITY",
    "EXCLUSION REASON",
]

FILE_PATH: Final = "/media/abc123xy/exclusion-list-august-2026.pdf"

SPANNING_ROW: Final = (2, 5)
"""(page, row) of the row drawn as one cell across the table."""


def ak_dhss_landing() -> bytes:
    """The Program Integrity page: navigation links and the list's one document link."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><title>Medicaid Program Integrity</title></head>
<body>
<nav><a href="/en/">Home</a> <a href="/en/office-of-the-commissioner/">Commissioner</a></nav>
<main>
<h1>Medicaid Program Integrity</h1>
<p>Providers excluded from the Alaska Medicaid program.</p>
<a href="{FILE_PATH}" title="Exclusion List August 2026" aria-label="document"
   target="_blank">Current Alaska Excluded provider list</a>
<a href="/en/contact-us/">Contact us</a>
</main>
</body></html>
""".encode()


def ak_dhss() -> bytes:
    """Three pages: the letterhead, then people (a suffix, other names in either name column,
    the reinstated "*"), organizations ("-" first name, a number, two lines, a "dba"), empty
    and "-" cells, a beneficiary, a row drawn as one cell and the footer note."""
    people = [
        HEADER,
        _row("1/15/2019", "TESTPERSON", "AVERY Q.", "PERSONAL CARE PROVIDER", "OIG"),
        _row(
            "10/2/2020", "SAMPLEMAN, JR", "JORDAN", "HEALTH CARE AIDE", "MFCU", "AS 47.05.210(a)(1)"
        ),
        _row(
            "3/4/2021",
            "MOCKWELL (aka Priormock)",
            "RILEY (aka Ree)",
            "PERSONAL CARE PROVIDER",
            "MFCU",
            "STATE SANCTION\nREASONS",
        ),
        _row("12/1/2022", "EXAMPLETON (FORMERMOCK)", "TATUM RAE", "SPEECH PATHOLOGY"),
        _row("7/7/2017", "QUINNMOCK", "BO (aka Bodie Lark)", "TRANSPORTATION AGENCY", "PI"),
        _row("5/5/2016", "*REINSTATEDMOCK", "MORGAN", "PERSONAL CARE PROVIDER"),
        _row(
            "8/9/2018",
            "SAMPLE HOME CARE, LLC",
            "-",
            "HOME HEALTH AGENCY",
            "PI",
            "AS 47.05.210(a)(5)",
        ),
        _row("2/3/2015", "417, LLC", "-", "HOME HEALTH AGENCY", "PI", "STATE SANCTION\nREASONS"),
    ]
    others = [
        HEADER,
        _row(
            "6/30/2020", "MOCK CARE\nSERVICES LLC", "-", "HEALTH PROFESSIONAL\nGROUP", "STATE", ""
        ),
        _row("4/1/2014", "TESTWELL", "DO AVERY", "-", "-", ""),
        _row("9/9/2019", "BENEFICIARYMOCK", "PAT ROWE", "RECIPIENT/BENEFICIARY"),
        _row(
            "11/12/2013", "SAMPLE DENTAL, LLC dba\nSMILE MOCK", "-", "DENTIST", "MFCU", "CONVICTION"
        ),
        _row("2/10/2010", "SPANMOCK", "CASEY", "HEALTH CARE AIDE"),
        ["NOTE: * MEANS THE\nINDIVIDUAL/ENTITY IS\nREINSTATED", "", "", "", "", ""],
    ]
    return build_table_pdf(
        [[], people, others], column_widths=COLUMN_WIDTHS, spanning_rows=[SPANNING_ROW]
    )


def _row(
    date: str,
    last: str,
    first: str,
    provider_type: str,
    authority: str = "OIG",
    reason: str = "SSA Section 1128(a)(1)",
) -> list[str]:
    return [date, last, first, provider_type, authority, reason]
