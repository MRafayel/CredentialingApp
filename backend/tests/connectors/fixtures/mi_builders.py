"""Synthetic Michigan MDHHS workbook in the shape read live on 2026-11-02, made-up rows.

One sheet, "Michigan": a title cell with line breaks, the header on row 2 (with "Sanction
Source" twice), then the data. Every name, NPI, licence and city here is invented.
"""

from datetime import date

from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C, NPI_D, Cell, workbook

HEADER = (
    "Entity Name",
    "Last Name",
    "First Name",
    "Middle Name",
    "Provider Category",
    "NPI#",
    "City",
    "License#",
    "Sanction Date1",
    "Sanction Source",
    "Sanction Date2",
    "Sanction Source",
    "Reason",
)

_NBSP = "\u00a0"
_ZWSP = "\u200b"


def _row(
    *,
    entity: str | None = None,
    last: str | None = None,
    first: str | None = None,
    middle: str | None = None,
    category: str = "Home Help",
    npi: Cell = None,
    city: str | None = "Mocktown",
    license: Cell = None,
    date1: Cell = date(2024, 3, 15),
    source1: str | None = "MDHHS",
    date2: Cell = None,
    source2: str | None = None,
    reason: str | None = None,
) -> tuple[Cell, ...]:
    return (
        entity,
        last,
        first,
        middle,
        category,
        npi,
        city,
        license,
        date1,
        source1,
        date2,
        source2,
        reason,
    )


def mi_mdhhs() -> bytes:
    """Every shape seen live: persons and businesses with their owners, a business listed
    once per owner (merged), owners one per line, aliases, odd licences and dates."""
    return workbook(
        [
            ("Medicaid Michigan Sanction Provider List\nOctober 29, 2026\nlist@example.test",),
            HEADER,
            _row(last="Testperson", first="Avery"),
            _row(
                last="Sampledoc",
                first="Jordan",
                middle="Q.",
                category="Physician",
                npi=int(NPI_A),
                license=4301000001,
                date1=date(2019, 7, 1),
                source1="MI-Licensing and Regulatory Affairs",
                date2="8/1/2019  9/12/2020",
                source2="MDHHS-OIG\nHHS-OIG",
                reason="Summary Suspension\nTerminated",
            ),
            _row(
                last="Mockley JR.",
                first="Casey",
                category="Physician",
                npi=f"{NPI_B}{_NBSP}{_NBSP}",
                city="Exampleton, OH",
                source1="HHS-OIG",
                reason="1128(a)(1)",
            ),
            _row(
                last="Sampleton (Priorton)",
                first="Robin (Rob)",
                middle="Lee",
                category="Dentist",
                license=f"2901{_ZWSP}000002",
                reason="Voluntarily Surrendered",
            ),
            _row(last="Testwell, MD", first="Morgan", category="Physician", reason="Excluded"),
            _row(
                entity="Sampleville Pharmacy Inc",
                category="Pharmacy",
                npi=int(NPI_C),
                license="NCPDP 1234567",
                city="Mockford ",
                source1="MDHHS-OIG",
                reason="Suspended",
            ),
            _row(
                entity="Example Care Pharmacy LLC",
                last="Ownermock",
                first="Dana",
                category="Pharmacy",
                npi=int(NPI_D),
                license=5301000003,
                source1="MDHHS-OIG",
                reason="Suspended",
            ),
            _row(
                entity="Example Care Pharmacy LLC",
                last="Partnermock",
                first="Lee",
                middle="J",
                category="Pharmacy",
                npi=int(NPI_D),
                license=5301000003,
                source1="MDHHS-OIG",
                date2="5/1/2025",
                source2="MDHHS-OIG",
                reason="Suspended",
            ),
            _row(
                entity="Mock Diagnostics Laboratory",
                last="Firstowner\nSecondowner",
                first="Alex\nJamie",
                category="Laboratory",
                license="12D3456789",
                date1="\n9/14/2021  ",
                source1="MDHHS-OIG",
                reason="1128(b)(4)",
            ),
            _row(
                entity="Sample Medical Group PC DBA Sample Family Clinic",
                category="Group",
                license=43.012345,
                source1="DCH",
            ),
            _row(
                entity="Mockdent, Avery J., DDS, PC (AKA Mockdent Smiles PC)",
                category="Dental Group",
                source1="DCH-OIG",
                reason="Terminated",
            ),
            _row(
                entity="Twin Mock Drugs",
                last="Twinmock",
                first="Ann/Lee",
                category="Pharmacy",
                license="12345 -MN",
                source1="Other State",
            ),
            _row(last="Datemock", first="Riley", date1="12/31/202"),
            _row(
                last="Licmock",
                first="Taylor",
                category="Nurse Practitioner",
                license="4704000004\n4704000005 (CS Only)",
                reason="Summary Suspension  Revoked",
            ),
            _row(first="Orphan"),
            (),
        ],
        title="Michigan",
    )
