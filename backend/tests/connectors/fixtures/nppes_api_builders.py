"""Synthetic NPPES API answers (version 2.1) in the real shape observed on 2026-11-07.

Keys, nesting and value formats follow the real answers: ISO dates in ``basic``, ``--`` for an
empty name prefix or suffix, epochs as strings of milliseconds, ``address_purpose``
``LOCATION``/``MAILING``, postal codes of 5 or 9 digits without a hyphen, taxonomy ``state``
and ``license`` that may be null, ``taxonomy_group`` ``""`` when none, and an ``Errors`` list
that echoes the request's input. Every person, organization and NPI is made up.
"""

import json
from typing import Any, Final

from tests.synthetic import make_npi

PERSON_NPI: Final = make_npi("170000001")
ORGANIZATION_NPI: Final = make_npi("170000002")

type Json = dict[str, Any]


def address(purpose: str, **fields: object) -> Json:
    """One address of a result."""
    return {
        "country_code": "US",
        "country_name": "United States",
        "address_purpose": purpose,
        "address_type": "DOM",
        "address_1": "100 SAMPLE WAY",
        "address_2": "SUITE 2",
        "city": "SAMPLEVILLE",
        "state": "NY",
        "postal_code": "999010000",
        "telephone_number": "555-010-0000",
        **fields,
    }


def taxonomy(code: str = "207Q00000X", **fields: object) -> Json:
    """One taxonomy of a result."""
    return {
        "code": code,
        "taxonomy_group": "",
        "desc": "Family Medicine",
        "state": "NY",
        "license": "SAMPLE-1",
        "primary": True,
        **fields,
    }


def individual_result(npi: str = PERSON_NPI, **basic: object) -> Json:
    """An active individual with both addresses, two taxonomies and a former name."""
    return {
        "created_epoch": "1131148800000",
        "enumeration_type": "NPI-1",
        "last_updated_epoch": "1767225600000",
        "number": npi,
        "addresses": [
            address("MAILING", address_1="PO BOX 9", postal_code="99901"),
            address("LOCATION"),
        ],
        "practiceLocations": [],
        "basic": {
            "first_name": "AVERY",
            "last_name": "TESTPERSON",
            "middle_name": "QUINN",
            "name_prefix": "Dr.",
            "name_suffix": "--",
            "credential": "MD",
            "sex": "F",
            "sole_proprietor": "NO",
            "enumeration_date": "2005-11-05",
            "last_updated": "2026-01-01",
            "certification_date": "2026-01-01",
            "status": "A",
            **basic,
        },
        "taxonomies": [
            taxonomy(),
            taxonomy("207R00000X", state="NJ", license=None, primary=False),
        ],
        "identifiers": [],
        "endpoints": [],
        "other_names": [
            {
                "type": "Former Name",
                "code": "1",
                "credential": "MD",
                "first_name": "AVERY",
                "last_name": "EXAMPLEFORMER",
                "middle_name": "QUINN",
                "prefix": "--",
                "suffix": "--",
            }
        ],
    }


def organization_result(npi: str = ORGANIZATION_NPI, **basic: object) -> Json:
    """An active organization with a doing-business-as name, a parent and an official."""
    return {
        "created_epoch": "1751846400000",
        "enumeration_type": "NPI-2",
        "last_updated_epoch": "1761004800000",
        "number": npi,
        "addresses": [
            address("MAILING"),
            address("LOCATION", postal_code="99902", telephone_number=None),
        ],
        "practiceLocations": [],
        "basic": {
            "organization_name": "SAMPLEVILLE CARE GROUP LLC",
            "organizational_subpart": "YES",
            "parent_organization_legal_business_name": "SAMPLEVILLE HEALTH SYSTEM",
            "enumeration_date": "2026-07-07",
            "last_updated": "2026-10-21",
            "certification_date": "2026-10-21",
            "status": "A",
            "authorized_official_first_name": "JORDAN",
            "authorized_official_last_name": "EXAMPLE",
            "authorized_official_telephone_number": "5550100001",
            "authorized_official_title_or_position": "ADMINISTRATOR",
            **basic,
        },
        "taxonomies": [
            taxonomy("261QM1300X", state=None, license=None),
            taxonomy(
                "193400000X", primary=False, taxonomy_group="193400000X - Single Specialty Group"
            ),
        ],
        "identifiers": [],
        "endpoints": [],
        "other_names": [
            {"type": "Doing Business As", "code": "3", "organization_name": "SAMPLEVILLE CLINIC"}
        ],
    }


def result(*results: Json) -> bytes:
    """An answer listing ``results``, with their count."""
    return json.dumps({"result_count": len(results), "results": list(results)}).encode()


def no_result() -> bytes:
    """The answer to an NPI that is not active (deactivated or never issued alike)."""
    return b'{"result_count":0,"results":[]}'


def errors(*items: tuple[str, str, str]) -> bytes:
    """An error answer: (description, field, the echoed input) per error."""
    return json.dumps(
        {
            "Errors": [
                {"description": description, "field": field, "number": echoed}
                for description, field, echoed in items
            ]
        }
    ).encode()
