"""The columns of the NPPES Data Dissemination file (V.2), as its header row spells them.

``HEADER`` is the header of the main CSV (``npidata_pfile_*.csv``) observed on 2026-11-06 in
the November 2026 monthly file: 330 columns. Column names are not personal data; they are
kept so the reader maps columns by name and fixtures have the real shape. Of them the mirror
keeps the columns named below. Telephone and fax numbers, the EIN, the sex code (the owner's
minimization rule), the 50 "Other Provider Identifier" groups and the parent organization's
TIN are never read.
"""

from collections.abc import Sequence
from typing import Final

from credentialing.domain.errors import DatasetFormatError

HEADER: Final[tuple[str, ...]] = (
    "NPI",
    "Entity Type Code",
    "Replacement NPI",
    "Employer Identification Number (EIN)",
    "Provider Organization Name (Legal Business Name)",
    "Provider Last Name (Legal Name)",
    "Provider First Name",
    "Provider Middle Name",
    "Provider Name Prefix Text",
    "Provider Name Suffix Text",
    "Provider Credential Text",
    "Provider Other Organization Name",
    "Provider Other Organization Name Type Code",
    "Provider Other Last Name",
    "Provider Other First Name",
    "Provider Other Middle Name",
    "Provider Other Name Prefix Text",
    "Provider Other Name Suffix Text",
    "Provider Other Credential Text",
    "Provider Other Last Name Type Code",
    "Provider First Line Business Mailing Address",
    "Provider Second Line Business Mailing Address",
    "Provider Business Mailing Address City Name",
    "Provider Business Mailing Address State Name",
    "Provider Business Mailing Address Postal Code",
    "Provider Business Mailing Address Country Code (If outside U.S.)",
    "Provider Business Mailing Address Telephone Number",
    "Provider Business Mailing Address Fax Number",
    "Provider First Line Business Practice Location Address",
    "Provider Second Line Business Practice Location Address",
    "Provider Business Practice Location Address City Name",
    "Provider Business Practice Location Address State Name",
    "Provider Business Practice Location Address Postal Code",
    "Provider Business Practice Location Address Country Code (If outside U.S.)",
    "Provider Business Practice Location Address Telephone Number",
    "Provider Business Practice Location Address Fax Number",
    "Provider Enumeration Date",
    "Last Update Date",
    "NPI Deactivation Reason Code",
    "NPI Deactivation Date",
    "NPI Reactivation Date",
    "Provider Sex Code",
    "Authorized Official Last Name",
    "Authorized Official First Name",
    "Authorized Official Middle Name",
    "Authorized Official Title or Position",
    "Authorized Official Telephone Number",
    "Healthcare Provider Taxonomy Code_1",
    "Provider License Number_1",
    "Provider License Number State Code_1",
    "Healthcare Provider Primary Taxonomy Switch_1",
    "Healthcare Provider Taxonomy Code_2",
    "Provider License Number_2",
    "Provider License Number State Code_2",
    "Healthcare Provider Primary Taxonomy Switch_2",
    "Healthcare Provider Taxonomy Code_3",
    "Provider License Number_3",
    "Provider License Number State Code_3",
    "Healthcare Provider Primary Taxonomy Switch_3",
    "Healthcare Provider Taxonomy Code_4",
    "Provider License Number_4",
    "Provider License Number State Code_4",
    "Healthcare Provider Primary Taxonomy Switch_4",
    "Healthcare Provider Taxonomy Code_5",
    "Provider License Number_5",
    "Provider License Number State Code_5",
    "Healthcare Provider Primary Taxonomy Switch_5",
    "Healthcare Provider Taxonomy Code_6",
    "Provider License Number_6",
    "Provider License Number State Code_6",
    "Healthcare Provider Primary Taxonomy Switch_6",
    "Healthcare Provider Taxonomy Code_7",
    "Provider License Number_7",
    "Provider License Number State Code_7",
    "Healthcare Provider Primary Taxonomy Switch_7",
    "Healthcare Provider Taxonomy Code_8",
    "Provider License Number_8",
    "Provider License Number State Code_8",
    "Healthcare Provider Primary Taxonomy Switch_8",
    "Healthcare Provider Taxonomy Code_9",
    "Provider License Number_9",
    "Provider License Number State Code_9",
    "Healthcare Provider Primary Taxonomy Switch_9",
    "Healthcare Provider Taxonomy Code_10",
    "Provider License Number_10",
    "Provider License Number State Code_10",
    "Healthcare Provider Primary Taxonomy Switch_10",
    "Healthcare Provider Taxonomy Code_11",
    "Provider License Number_11",
    "Provider License Number State Code_11",
    "Healthcare Provider Primary Taxonomy Switch_11",
    "Healthcare Provider Taxonomy Code_12",
    "Provider License Number_12",
    "Provider License Number State Code_12",
    "Healthcare Provider Primary Taxonomy Switch_12",
    "Healthcare Provider Taxonomy Code_13",
    "Provider License Number_13",
    "Provider License Number State Code_13",
    "Healthcare Provider Primary Taxonomy Switch_13",
    "Healthcare Provider Taxonomy Code_14",
    "Provider License Number_14",
    "Provider License Number State Code_14",
    "Healthcare Provider Primary Taxonomy Switch_14",
    "Healthcare Provider Taxonomy Code_15",
    "Provider License Number_15",
    "Provider License Number State Code_15",
    "Healthcare Provider Primary Taxonomy Switch_15",
    "Other Provider Identifier_1",
    "Other Provider Identifier Type Code_1",
    "Other Provider Identifier State_1",
    "Other Provider Identifier Issuer_1",
    "Other Provider Identifier_2",
    "Other Provider Identifier Type Code_2",
    "Other Provider Identifier State_2",
    "Other Provider Identifier Issuer_2",
    "Other Provider Identifier_3",
    "Other Provider Identifier Type Code_3",
    "Other Provider Identifier State_3",
    "Other Provider Identifier Issuer_3",
    "Other Provider Identifier_4",
    "Other Provider Identifier Type Code_4",
    "Other Provider Identifier State_4",
    "Other Provider Identifier Issuer_4",
    "Other Provider Identifier_5",
    "Other Provider Identifier Type Code_5",
    "Other Provider Identifier State_5",
    "Other Provider Identifier Issuer_5",
    "Other Provider Identifier_6",
    "Other Provider Identifier Type Code_6",
    "Other Provider Identifier State_6",
    "Other Provider Identifier Issuer_6",
    "Other Provider Identifier_7",
    "Other Provider Identifier Type Code_7",
    "Other Provider Identifier State_7",
    "Other Provider Identifier Issuer_7",
    "Other Provider Identifier_8",
    "Other Provider Identifier Type Code_8",
    "Other Provider Identifier State_8",
    "Other Provider Identifier Issuer_8",
    "Other Provider Identifier_9",
    "Other Provider Identifier Type Code_9",
    "Other Provider Identifier State_9",
    "Other Provider Identifier Issuer_9",
    "Other Provider Identifier_10",
    "Other Provider Identifier Type Code_10",
    "Other Provider Identifier State_10",
    "Other Provider Identifier Issuer_10",
    "Other Provider Identifier_11",
    "Other Provider Identifier Type Code_11",
    "Other Provider Identifier State_11",
    "Other Provider Identifier Issuer_11",
    "Other Provider Identifier_12",
    "Other Provider Identifier Type Code_12",
    "Other Provider Identifier State_12",
    "Other Provider Identifier Issuer_12",
    "Other Provider Identifier_13",
    "Other Provider Identifier Type Code_13",
    "Other Provider Identifier State_13",
    "Other Provider Identifier Issuer_13",
    "Other Provider Identifier_14",
    "Other Provider Identifier Type Code_14",
    "Other Provider Identifier State_14",
    "Other Provider Identifier Issuer_14",
    "Other Provider Identifier_15",
    "Other Provider Identifier Type Code_15",
    "Other Provider Identifier State_15",
    "Other Provider Identifier Issuer_15",
    "Other Provider Identifier_16",
    "Other Provider Identifier Type Code_16",
    "Other Provider Identifier State_16",
    "Other Provider Identifier Issuer_16",
    "Other Provider Identifier_17",
    "Other Provider Identifier Type Code_17",
    "Other Provider Identifier State_17",
    "Other Provider Identifier Issuer_17",
    "Other Provider Identifier_18",
    "Other Provider Identifier Type Code_18",
    "Other Provider Identifier State_18",
    "Other Provider Identifier Issuer_18",
    "Other Provider Identifier_19",
    "Other Provider Identifier Type Code_19",
    "Other Provider Identifier State_19",
    "Other Provider Identifier Issuer_19",
    "Other Provider Identifier_20",
    "Other Provider Identifier Type Code_20",
    "Other Provider Identifier State_20",
    "Other Provider Identifier Issuer_20",
    "Other Provider Identifier_21",
    "Other Provider Identifier Type Code_21",
    "Other Provider Identifier State_21",
    "Other Provider Identifier Issuer_21",
    "Other Provider Identifier_22",
    "Other Provider Identifier Type Code_22",
    "Other Provider Identifier State_22",
    "Other Provider Identifier Issuer_22",
    "Other Provider Identifier_23",
    "Other Provider Identifier Type Code_23",
    "Other Provider Identifier State_23",
    "Other Provider Identifier Issuer_23",
    "Other Provider Identifier_24",
    "Other Provider Identifier Type Code_24",
    "Other Provider Identifier State_24",
    "Other Provider Identifier Issuer_24",
    "Other Provider Identifier_25",
    "Other Provider Identifier Type Code_25",
    "Other Provider Identifier State_25",
    "Other Provider Identifier Issuer_25",
    "Other Provider Identifier_26",
    "Other Provider Identifier Type Code_26",
    "Other Provider Identifier State_26",
    "Other Provider Identifier Issuer_26",
    "Other Provider Identifier_27",
    "Other Provider Identifier Type Code_27",
    "Other Provider Identifier State_27",
    "Other Provider Identifier Issuer_27",
    "Other Provider Identifier_28",
    "Other Provider Identifier Type Code_28",
    "Other Provider Identifier State_28",
    "Other Provider Identifier Issuer_28",
    "Other Provider Identifier_29",
    "Other Provider Identifier Type Code_29",
    "Other Provider Identifier State_29",
    "Other Provider Identifier Issuer_29",
    "Other Provider Identifier_30",
    "Other Provider Identifier Type Code_30",
    "Other Provider Identifier State_30",
    "Other Provider Identifier Issuer_30",
    "Other Provider Identifier_31",
    "Other Provider Identifier Type Code_31",
    "Other Provider Identifier State_31",
    "Other Provider Identifier Issuer_31",
    "Other Provider Identifier_32",
    "Other Provider Identifier Type Code_32",
    "Other Provider Identifier State_32",
    "Other Provider Identifier Issuer_32",
    "Other Provider Identifier_33",
    "Other Provider Identifier Type Code_33",
    "Other Provider Identifier State_33",
    "Other Provider Identifier Issuer_33",
    "Other Provider Identifier_34",
    "Other Provider Identifier Type Code_34",
    "Other Provider Identifier State_34",
    "Other Provider Identifier Issuer_34",
    "Other Provider Identifier_35",
    "Other Provider Identifier Type Code_35",
    "Other Provider Identifier State_35",
    "Other Provider Identifier Issuer_35",
    "Other Provider Identifier_36",
    "Other Provider Identifier Type Code_36",
    "Other Provider Identifier State_36",
    "Other Provider Identifier Issuer_36",
    "Other Provider Identifier_37",
    "Other Provider Identifier Type Code_37",
    "Other Provider Identifier State_37",
    "Other Provider Identifier Issuer_37",
    "Other Provider Identifier_38",
    "Other Provider Identifier Type Code_38",
    "Other Provider Identifier State_38",
    "Other Provider Identifier Issuer_38",
    "Other Provider Identifier_39",
    "Other Provider Identifier Type Code_39",
    "Other Provider Identifier State_39",
    "Other Provider Identifier Issuer_39",
    "Other Provider Identifier_40",
    "Other Provider Identifier Type Code_40",
    "Other Provider Identifier State_40",
    "Other Provider Identifier Issuer_40",
    "Other Provider Identifier_41",
    "Other Provider Identifier Type Code_41",
    "Other Provider Identifier State_41",
    "Other Provider Identifier Issuer_41",
    "Other Provider Identifier_42",
    "Other Provider Identifier Type Code_42",
    "Other Provider Identifier State_42",
    "Other Provider Identifier Issuer_42",
    "Other Provider Identifier_43",
    "Other Provider Identifier Type Code_43",
    "Other Provider Identifier State_43",
    "Other Provider Identifier Issuer_43",
    "Other Provider Identifier_44",
    "Other Provider Identifier Type Code_44",
    "Other Provider Identifier State_44",
    "Other Provider Identifier Issuer_44",
    "Other Provider Identifier_45",
    "Other Provider Identifier Type Code_45",
    "Other Provider Identifier State_45",
    "Other Provider Identifier Issuer_45",
    "Other Provider Identifier_46",
    "Other Provider Identifier Type Code_46",
    "Other Provider Identifier State_46",
    "Other Provider Identifier Issuer_46",
    "Other Provider Identifier_47",
    "Other Provider Identifier Type Code_47",
    "Other Provider Identifier State_47",
    "Other Provider Identifier Issuer_47",
    "Other Provider Identifier_48",
    "Other Provider Identifier Type Code_48",
    "Other Provider Identifier State_48",
    "Other Provider Identifier Issuer_48",
    "Other Provider Identifier_49",
    "Other Provider Identifier Type Code_49",
    "Other Provider Identifier State_49",
    "Other Provider Identifier Issuer_49",
    "Other Provider Identifier_50",
    "Other Provider Identifier Type Code_50",
    "Other Provider Identifier State_50",
    "Other Provider Identifier Issuer_50",
    "Is Sole Proprietor",
    "Is Organization Subpart",
    "Parent Organization LBN",
    "Parent Organization TIN",
    "Authorized Official Name Prefix Text",
    "Authorized Official Name Suffix Text",
    "Authorized Official Credential Text",
    "Healthcare Provider Taxonomy Group_1",
    "Healthcare Provider Taxonomy Group_2",
    "Healthcare Provider Taxonomy Group_3",
    "Healthcare Provider Taxonomy Group_4",
    "Healthcare Provider Taxonomy Group_5",
    "Healthcare Provider Taxonomy Group_6",
    "Healthcare Provider Taxonomy Group_7",
    "Healthcare Provider Taxonomy Group_8",
    "Healthcare Provider Taxonomy Group_9",
    "Healthcare Provider Taxonomy Group_10",
    "Healthcare Provider Taxonomy Group_11",
    "Healthcare Provider Taxonomy Group_12",
    "Healthcare Provider Taxonomy Group_13",
    "Healthcare Provider Taxonomy Group_14",
    "Healthcare Provider Taxonomy Group_15",
    "Certification Date",
)
"""The main CSV's header row, in order."""

TAXONOMY_GROUPS: Final = 15
"""Taxonomy (with licence) groups per row: ``Healthcare Provider Taxonomy Code_1`` to ``_15``."""

NPI: Final = "NPI"
ENTITY_TYPE: Final = "Entity Type Code"
REPLACEMENT_NPI: Final = "Replacement NPI"
ORGANIZATION_NAME: Final = "Provider Organization Name (Legal Business Name)"
LAST_NAME: Final = "Provider Last Name (Legal Name)"
FIRST_NAME: Final = "Provider First Name"
MIDDLE_NAME: Final = "Provider Middle Name"
NAME_PREFIX: Final = "Provider Name Prefix Text"
NAME_SUFFIX: Final = "Provider Name Suffix Text"
CREDENTIAL: Final = "Provider Credential Text"
OTHER_ORGANIZATION_NAME: Final = "Provider Other Organization Name"
OTHER_LAST_NAME: Final = "Provider Other Last Name"
OTHER_FIRST_NAME: Final = "Provider Other First Name"
OTHER_MIDDLE_NAME: Final = "Provider Other Middle Name"
MAILING_LINE1: Final = "Provider First Line Business Mailing Address"
MAILING_LINE2: Final = "Provider Second Line Business Mailing Address"
MAILING_CITY: Final = "Provider Business Mailing Address City Name"
MAILING_STATE: Final = "Provider Business Mailing Address State Name"
MAILING_POSTAL_CODE: Final = "Provider Business Mailing Address Postal Code"
MAILING_COUNTRY: Final = "Provider Business Mailing Address Country Code (If outside U.S.)"
PRACTICE_LINE1: Final = "Provider First Line Business Practice Location Address"
PRACTICE_LINE2: Final = "Provider Second Line Business Practice Location Address"
PRACTICE_CITY: Final = "Provider Business Practice Location Address City Name"
PRACTICE_STATE: Final = "Provider Business Practice Location Address State Name"
PRACTICE_POSTAL_CODE: Final = "Provider Business Practice Location Address Postal Code"
PRACTICE_COUNTRY: Final = (
    "Provider Business Practice Location Address Country Code (If outside U.S.)"
)
ENUMERATION_DATE: Final = "Provider Enumeration Date"
LAST_UPDATE_DATE: Final = "Last Update Date"
DEACTIVATION_REASON: Final = "NPI Deactivation Reason Code"
DEACTIVATION_DATE: Final = "NPI Deactivation Date"
REACTIVATION_DATE: Final = "NPI Reactivation Date"
OFFICIAL_LAST_NAME: Final = "Authorized Official Last Name"
OFFICIAL_FIRST_NAME: Final = "Authorized Official First Name"
OFFICIAL_MIDDLE_NAME: Final = "Authorized Official Middle Name"
OFFICIAL_TITLE: Final = "Authorized Official Title or Position"
OFFICIAL_PREFIX: Final = "Authorized Official Name Prefix Text"
OFFICIAL_SUFFIX: Final = "Authorized Official Name Suffix Text"
OFFICIAL_CREDENTIAL: Final = "Authorized Official Credential Text"
SOLE_PROPRIETOR: Final = "Is Sole Proprietor"
ORGANIZATION_SUBPART: Final = "Is Organization Subpart"
PARENT_ORGANIZATION_NAME: Final = "Parent Organization LBN"
CERTIFICATION_DATE: Final = "Certification Date"


def taxonomy_code(group: int) -> str:
    """The taxonomy code column of group ``group`` (1 to 15)."""
    return f"Healthcare Provider Taxonomy Code_{group}"


def license_number(group: int) -> str:
    """The licence number column of group ``group``."""
    return f"Provider License Number_{group}"


def license_state(group: int) -> str:
    """The licence state column of group ``group``."""
    return f"Provider License Number State Code_{group}"


def primary_switch(group: int) -> str:
    """The primary-taxonomy switch column (``Y``/``N``) of group ``group``."""
    return f"Healthcare Provider Primary Taxonomy Switch_{group}"


def taxonomy_group(group: int) -> str:
    """The taxonomy group column of group ``group``."""
    return f"Healthcare Provider Taxonomy Group_{group}"


READ_COLUMNS: Final[tuple[str, ...]] = (
    NPI,
    ENTITY_TYPE,
    REPLACEMENT_NPI,
    ORGANIZATION_NAME,
    LAST_NAME,
    FIRST_NAME,
    MIDDLE_NAME,
    NAME_PREFIX,
    NAME_SUFFIX,
    CREDENTIAL,
    OTHER_ORGANIZATION_NAME,
    OTHER_LAST_NAME,
    OTHER_FIRST_NAME,
    OTHER_MIDDLE_NAME,
    MAILING_LINE1,
    MAILING_LINE2,
    MAILING_CITY,
    MAILING_STATE,
    MAILING_POSTAL_CODE,
    MAILING_COUNTRY,
    PRACTICE_LINE1,
    PRACTICE_LINE2,
    PRACTICE_CITY,
    PRACTICE_STATE,
    PRACTICE_POSTAL_CODE,
    PRACTICE_COUNTRY,
    ENUMERATION_DATE,
    LAST_UPDATE_DATE,
    DEACTIVATION_REASON,
    DEACTIVATION_DATE,
    REACTIVATION_DATE,
    OFFICIAL_LAST_NAME,
    OFFICIAL_FIRST_NAME,
    OFFICIAL_MIDDLE_NAME,
    OFFICIAL_TITLE,
    OFFICIAL_PREFIX,
    OFFICIAL_SUFFIX,
    OFFICIAL_CREDENTIAL,
    SOLE_PROPRIETOR,
    ORGANIZATION_SUBPART,
    PARENT_ORGANIZATION_NAME,
    CERTIFICATION_DATE,
    *(
        column(group)
        for group in range(1, TAXONOMY_GROUPS + 1)
        for column in (taxonomy_code, license_number, license_state, primary_switch, taxonomy_group)
    ),
)
"""Every column the reader maps; the header must have each of them."""


def header_index(header: Sequence[str]) -> dict[str, int]:
    """The position of every column in ``header``, checked to contain :data:`READ_COLUMNS`.

    Columns are found by name, so CMS may add or move columns without breaking the reader.

    Raises:
        DatasetFormatError: A column the reader needs is missing (named), or a name repeats.
    """
    index = {name: position for position, name in enumerate(header)}
    if len(index) != len(header):
        raise DatasetFormatError("the header repeats a column name")
    missing = [name for name in READ_COLUMNS if name not in index]
    if missing:
        raise DatasetFormatError(f"the header lacks the columns {', '.join(missing)}")
    return index
