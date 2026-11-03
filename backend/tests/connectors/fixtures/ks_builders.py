"""A synthetic Kansas termination workbook in the shape observed on 2026-11-02, made-up rows.

Sheet "Terminations": "REVISED: <date>" alone in G1, the header with colons on row 2, Excel
dates, "N/A" for a missing NPI or KMAP number, names first name first, several names joined
by " / ", D/B/A cells listing several businesses, several identifiers in one cell.
"""

from datetime import date

from tests.connectors.fixtures.builders import (
    INVALID_TEN_DIGITS,
    NPI_A,
    NPI_B,
    NPI_C,
    NPI_D,
    NPI_E,
    Rows,
    workbook,
)
from tests.synthetic import make_npi

COLUMNS = (
    "Termination Date:",
    "D/B/A Business Name:",
    "Name:",
    "Provider Type:",
    "KMAP Provider #:",
    "NPI #:",
    "Comments:",
)

NPI_G = make_npi("100000007")


def ks_kmap_rows() -> Rows:
    """The sheet's rows, title row and header included."""
    return [
        (None, None, None, None, None, None, "REVISED: October 21, 2026"),
        COLUMNS,
        (
            date(2021, 3, 4),
            None,
            "Avery J. Testperson",
            "Nurse/Nurse Aide",
            "N/A",
            "N/A",
            "HHS-OIG excluded participation.",
        ),
        (
            date(2019, 6, 1),
            None,
            "Jordan Mockname / Jordan Samplewed ",
            "N/A",
            "N/A",
            "N/A",
            "Loss of license or other State action.",
        ),
        (
            date(2018, 2, 2),
            None,
            "Sample Medical Lab, LLC",
            "Lab",
            "100000001A,B,C",
            int(NPI_A),
            "Medicare Revocation.",
        ),
        (
            date(2020, 11, 30),
            "Sample Home Care, Inc., Sample Home Health Services, Sample Associates, Inc.",
            "Riley Exampleowner",
            "Home Health, HCBS",
            "100000002A; 100000003A",
            f"{NPI_B}; {NPI_C}",
            "Non-compliance with provider agreement.  Violation of KMAP regulation/policy.",
        ),
        (
            date(2022, 8, 15),
            "Mock & Test, Inc. / Test of Kansas,Inc.",
            "Casey Q Sampleperson",
            "Audiology",
            "100000004A (old);       100000005A",
            f"{NPI_D} for the clinic;        n/a for home",
            "Terminated under a separate Medicaid enrollment.",
        ),
        (date(2017, 1, 9), None, "Samplelabs", "Lab", "N/A", int(NPI_E), "Medicare Revocation"),
        (
            date(2016, 5, 5),
            None,
            "Quinn Fakename Jr.",
            "Physician",
            12345678901234,
            "N/A",
            "HHS-OIG excluded participation.",
        ),
        (
            "10/20/2026",
            None,
            "Morgan Testdoc, MD",
            "Physician",
            "N/A",
            f"{NPI_G};\n{INVALID_TEN_DIGITS}",
            "Medicare billing privileges revoked.",
        ),
        (
            date(2015, 7, 7),
            "Sample Clinic",
            "Sample Medical (Sample Healthcare)",
            "X-Ray Clinic",
            "100000006A",
            "N/A",
            "HHS-OIG excluded participation.",
        ),
        (
            "2026-13-45",
            None,
            "Taylor Mockperson",
            "PCA",
            "N/A",
            "N/A",
            "HHS-OIG excluded participation.",
        ),
        (date(2023, 4, 4), None, None, "Physician", "N/A", "N/A", "Medicare Revocation."),
        (
            date(2024, 9, 9),
            None,
            "Drew Duplicate",
            "Counselor",
            "100000008A",
            "N/A",
            "Loss of license or other State action.",
        ),
        (
            date(2024, 9, 9),
            None,
            "Drew Duplicate",
            "Mental Health Provider",
            "100000008A",
            "N/A",
            "Terminated under a separate Medicaid enrollment.",
        ),
        (),
    ]


def ks_kmap() -> bytes:
    """Kansas: the termination workbook, every observed shape on made-up rows."""
    return workbook(ks_kmap_rows(), title="Terminations")
