"""A synthetic New Hampshire sanctions workbook in the shape seen on 2026-11-02, made-up rows.

The real file has a title cell ("Updated 10/26/26") above the header, Excel dates (one cell
with two dates typed as text), NPIs as numbers, as text, as "N/A" or as an organization's FEIN,
a business on a few person rows, and one person repeated with several NPIs. Every name and
identifier here is invented.
"""

from datetime import date

from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C, NPI_D, NPI_E, NPI_F, workbook
from tests.synthetic import make_npi

COLUMNS = (
    "Provider/Individual Last Name",
    "Provider/Individual First Name",
    "Business Name",
    "Provider/Individual Type",
    "National Provider Identifier (NPI)",
    "Exclusion/Sanction Reason",
    "Exclusion/Sanction Effective Date",
)

NPI_G = make_npi("100000007")

FAKE_FEIN = "00-0000000 (FEIN)"
"""An organization's federal employer number in the NPI column, as one real row has (made up)."""


def nh_dhhs() -> bytes:
    """Persons and organizations in every shape observed; 13 data rows, one of them unusable."""
    return workbook(
        [
            ("Updated 10/26/26",),
            COLUMNS,
            (
                "Testperson",
                "Avery J.",
                None,
                "Physician",
                int(NPI_A),
                "Medicare Revocation",
                date(2019, 1, 7),
            ),
            (
                "Sampleton Jr ",
                "Jordan",
                None,
                "Dentist",
                NPI_B,
                "OIG LEIE and SAM Exclusion",
                date(2021, 5, 3),
            ),
            (
                "Mockfield, IV",
                "Riley Ann",
                None,
                "RECIPT/BENEFICIARY",
                "N/A",
                "Medicaid Fraud conviction",
                "3/15/2024",
            ),
            (
                "Exampleton",
                "Casey",
                "Exampleton Home Care LLC",
                "Home Care Service Provider-owner",
                "n/a",
                "Board action Voluntary surrender of license",
                date(2018, 2, 1),
            ),
            (
                None,
                None,
                "Sample Medical Supply, INC.",
                "Medical Supplier",
                FAKE_FEIN,
                "Mandatory Exclusion from Medicaid",
                date(2016, 9, 30),
            ),
            (
                None,
                None,
                "Sampletown Care @ Home, LLC",
                "Home Care Service Provider",
                None,
                "Medicare Revocation",
                date(2017, 4, 12),
            ),
            (
                "Fakerly",
                "Morgan",
                None,
                "LPN",
                None,
                "Program Related Conviction 9/19/23 OIG",
                "1/02/2020        3/04/2021",
            ),
            (
                "Repeatson",
                "Quinn",
                None,
                "Physician",
                int(NPI_D),
                "Medicare Revocation",
                date(2014, 11, 14),
            ),
            (
                "Repeatson",
                "Quinn",
                None,
                "Physician",
                int(NPI_E),
                "Medicare Revocation",
                date(2014, 11, 14),
            ),
            (
                "Repeatson",
                "Quinn",
                None,
                "Physician",
                int(NPI_F),
                "Medicare Revocation",
                date(2013, 11, 6),
            ),
            (
                "Repeatson",
                "Quinn",
                None,
                "Physician Assistant",
                int(NPI_F),
                "Medicare revocation (CMS)",
                date(2013, 11, 6),
            ),
            (
                "Doubleton",
                "Sam",
                None,
                "Medical Supplier",
                f"{NPI_C}, {NPI_G}",
                "Suspension of license",
                date(2022, 8, 9),
            ),
            (None, None, None, None, None, None, None),
            (None, "Lonely", None, "Physician", None, "Medicare Revocation", date(2020, 1, 1)),
        ]
    )
