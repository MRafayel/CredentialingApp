"""Synthetic NPPES files in the real shape (2026-11-06), built at test time.

The CSV has the real 330-column V.2 header, every field double-quoted, LF line endings and the
header as the first line, inside a zip with the header file, a reference file and a readme
beside it, as CMS publishes it. The deactivation workbook has the title row, the header and
text dates. Every person, organization and NPI is made up.
"""

import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from typing import Final

from credentialing.connectors.nppes import columns as c
from tests.connectors.fixtures.builders import workbook
from tests.synthetic import make_npi

PERSON_NPI: Final = make_npi("150000001")
ORGANIZATION_NPI: Final = make_npi("150000002")
STUB_NPI: Final = make_npi("150000003")
REACTIVATED_NPI: Final = make_npi("150000004")
QUOTED_NPI: Final = make_npi("150000005")
MULTILINE_NPI: Final = make_npi("150000006")
REPLACEMENT_NPI: Final = make_npi("150000007")

MAIN_MEMBER: Final = "npidata_pfile_20050523-20260105.csv"
WEEKLY_MEMBER: Final = "npidata_pfile_20251229-20260104.csv"
DEACTIVATION_MEMBER: Final = "NPPES Deactivated NPI Report 20260105.xlsx"

type Row = Mapping[str, str]


def person(npi: str = PERSON_NPI, **fields: str) -> dict[str, str]:
    """An individual with two taxonomies (licences in two states), both addresses and an
    other name."""
    row = {
        c.NPI: npi,
        c.ENTITY_TYPE: "1",
        c.LAST_NAME: "TESTPERSON",
        c.FIRST_NAME: "AVERY",
        c.MIDDLE_NAME: "Q",
        c.NAME_PREFIX: "DR.",
        c.NAME_SUFFIX: "JR.",
        c.CREDENTIAL: "M.D.",
        c.OTHER_LAST_NAME: "SAMPLEFORD",
        c.OTHER_FIRST_NAME: "AVERY",
        c.MAILING_LINE1: "100 SAMPLE WAY",
        c.MAILING_LINE2: "SUITE 2",
        c.MAILING_CITY: "SAMPLEVILLE",
        c.MAILING_STATE: "NY",
        c.MAILING_POSTAL_CODE: "120010001",
        c.MAILING_COUNTRY: "US",
        c.PRACTICE_LINE1: "200 EXAMPLE ROAD",
        c.PRACTICE_CITY: "EXAMPLETON",
        c.PRACTICE_STATE: "NJ",
        c.PRACTICE_POSTAL_CODE: "07001",
        c.PRACTICE_COUNTRY: "US",
        c.ENUMERATION_DATE: "05/23/2007",
        c.LAST_UPDATE_DATE: "07/08/2025",
        c.CERTIFICATION_DATE: "07/08/2025",
        c.SOLE_PROPRIETOR: "N",
        c.taxonomy_code(1): "207R00000X",
        c.license_number(1): "SAMPLE-1234",
        c.license_state(1): "NY",
        c.primary_switch(1): "Y",
        c.taxonomy_code(2): "207RC0000X",
        c.license_number(2): "XX99",
        c.license_state(2): "NJ",
        c.primary_switch(2): "N",
        c.taxonomy_group(2): "193200000X MULTI-SPECIALTY GROUP",
    }
    row.update(fields)
    return row


def organization(npi: str = ORGANIZATION_NPI, **fields: str) -> dict[str, str]:
    """An organization subpart with an authorized official, an other name and a parent."""
    row = {
        c.NPI: npi,
        c.ENTITY_TYPE: "2",
        c.ORGANIZATION_NAME: "SAMPLEVILLE CARE GROUP LLC",
        c.OTHER_ORGANIZATION_NAME: "SAMPLEVILLE CLINIC",
        c.PARENT_ORGANIZATION_NAME: "SAMPLEVILLE HEALTH HOLDINGS",
        c.ORGANIZATION_SUBPART: "Y",
        c.OFFICIAL_LAST_NAME: "EXAMPLE",
        c.OFFICIAL_FIRST_NAME: "JORDAN",
        c.OFFICIAL_TITLE: "ADMINISTRATOR",
        c.OFFICIAL_PREFIX: "MS.",
        c.OFFICIAL_CREDENTIAL: "MBA",
        c.PRACTICE_LINE1: "1 EXAMPLE PLAZA",
        c.PRACTICE_CITY: "SAMPLE CITY",
        c.PRACTICE_STATE: "ONTARIO",
        c.PRACTICE_POSTAL_CODE: "K1A0B1",
        c.PRACTICE_COUNTRY: "CA",
        c.ENUMERATION_DATE: "01/02/2010",
        c.LAST_UPDATE_DATE: "03/04/2024",
        c.taxonomy_code(1): "261QM1300X",
        c.primary_switch(1): "Y",
    }
    row.update(fields)
    return row


def stub(npi: str = STUB_NPI, deactivated: str = "06/15/2019") -> dict[str, str]:
    """A deactivated NPI: only the NPI and the deactivation date, as the registry blanks it."""
    return {c.NPI: npi, c.DEACTIVATION_DATE: deactivated}


def reactivated(npi: str = REACTIVATED_NPI) -> dict[str, str]:
    """An individual deactivated once and reactivated since: active again."""
    return person(
        npi,
        **{
            c.LAST_NAME: "SAMPLEWOOD",
            c.FIRST_NAME: "RILEY",
            c.DEACTIVATION_DATE: "02/01/2020",
            c.REACTIVATION_DATE: "03/01/2020",
            c.REPLACEMENT_NPI: REPLACEMENT_NPI,
        },
    )


def quoted(npi: str = QUOTED_NPI) -> dict[str, str]:
    """An organization whose name holds a quote and a comma."""
    return organization(npi, **{c.ORGANIZATION_NAME: 'SAMPLE "CARE", INC'})


def multiline(npi: str = MULTILINE_NPI) -> dict[str, str]:
    """An individual whose address line spans two physical lines."""
    return person(npi, **{c.MAILING_LINE1: "100 SAMPLE WAY\nBUILDING B"})


def monthly_rows() -> list[dict[str, str]]:
    """A monthly file's rows: one of each shape."""
    return [person(), organization(), stub(), reactivated(), quoted(), multiline()]


def nppes_csv(rows: Sequence[Row], *, header: Sequence[str] = c.HEADER) -> bytes:
    """The main CSV: the header line, then one line per row, every field quoted."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(name, "") for name in header])
    return buffer.getvalue().encode()


def nppes_zip(
    rows: Sequence[Row] | None = None,
    *,
    member: str = MAIN_MEMBER,
    body: bytes | None = None,
) -> bytes:
    """A monthly (or weekly) zip: the main CSV, its header file, a reference file and a
    readme, as CMS lays it out."""
    main = body if body is not None else nppes_csv(monthly_rows() if rows is None else rows)
    stem = member.removesuffix(".csv")
    other = stem.replace("npidata_pfile", "othername_pfile")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, main)
        archive.writestr(f"{stem}_fileheader.csv", nppes_csv([]))
        archive.writestr(f"{other}.csv", b'"NPI","Provider Other Organization Name"\n')
        archive.writestr("NPPES_Data_Dissemination_Readme_v.2.pdf", b"%PDF-1.4 synthetic")
    return buffer.getvalue()


def deactivation_zip(
    rows: Sequence[tuple[str, str]] = ((STUB_NPI, "06/15/2019"), (PERSON_NPI, "01/05/2026")),
    *,
    header: tuple[str, str] = ("NPI", "NPPES Deactivation Date"),
) -> bytes:
    """The deactivation zip: one workbook with the title row, the header and text dates."""
    book = workbook(
        [("NPPES Deactivated Records as of Jan 05 2026",), header, *rows],
        title="DeactivatedNPIs",
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(DEACTIVATION_MEMBER, book)
    return buffer.getvalue()


def files_page(
    *,
    monthly: str = "./NPPES_Data_Dissemination_January_2026_V2.zip",
    weekly: str = "./NPPES_Data_Dissemination_122925_010426_Weekly_V2.zip",
    deactivations: str = "./NPPES_Deactivated_NPI_Report_010526_V2.zip",
) -> bytes:
    """The files page, its links as CMS writes them (relative, single-quoted, with ids)."""
    return f"""<html><body><h1>NPPES Downloadable File</h1>
<a href="#main-content">Skip</a>
<p><a id='DDSMTH.ZIP.D' href='{monthly}' aria-label='NPPES Data Dissemination V.2'>
NPPES Data Dissemination V.2 (January 05, 2026)</a></p>
<p><a id='DDSDEACTIVATEMTH.ZIP.D' href='{deactivations}'>Monthly Deactivation Update</a></p>
<p><a id='DDSWKLY.ZIP.D' href='{weekly}'>Weekly Update - 122925_010426</a></p>
<a href="https://nppes.cms.hhs.gov/NppesSubscription/subscribe">Subscribe</a>
</body></html>""".encode()
