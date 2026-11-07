import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.connectors.nppes import (
    NppesDeactivationsConnector,
    NppesMonthlyConnector,
    NppesWeeklyConnector,
)
from credentialing.connectors.nppes import columns as c
from credentialing.connectors.nppes.deactivations import NppesDeactivationDataset
from credentialing.connectors.nppes.reader import NppesCsvDataset
from credentialing.domain.enums import (
    ChangeSignal,
    CredentialType,
    DatasetLoadMode,
    EntityKind,
    NpiStatus,
    Outcome,
)
from credentialing.domain.errors import DatasetFormatError
from credentialing.domain.models import (
    Address,
    AuthorizedOfficial,
    NpiTaxonomy,
    PersonName,
)
from tests.connectors.fixtures import nppes_builders as b
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW, make_npi

pytestmark = pytest.mark.anyio

SNAPSHOT = uuid4()


def _zip(tmp_path: Path, content: bytes, name: str = "monthly.zip") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def _dataset(tmp_path: Path, rows: list[dict[str, str]] | None = None) -> NppesCsvDataset:
    return NppesCsvDataset(_zip(tmp_path, b.nppes_zip(rows)), SNAPSHOT)


def _lines(rows: list[dict[str, str]]) -> list[bytes]:
    """Each row's record bytes as the file holds them (line ending stripped)."""
    return [b.nppes_csv([row]).split(b"\n", 1)[1].removesuffix(b"\n") for row in rows]


def test_the_header_holds_every_column_the_reader_maps() -> None:
    assert len(c.HEADER) == 330
    assert c.HEADER[0] == c.NPI
    assert c.HEADER[-1] == c.CERTIFICATION_DATE
    assert set(c.READ_COLUMNS) <= set(c.HEADER)
    assert c.header_index(c.HEADER)[c.taxonomy_group(15)] == 328


def test_scan_yields_every_npi_and_the_hash_of_its_exact_line(tmp_path: Path) -> None:
    rows = b.monthly_rows()

    scanned = list(_dataset(tmp_path, rows).scan())

    assert [npi for npi, _ in scanned] == [row[c.NPI] for row in rows]
    assert [line_hash for _, line_hash in scanned] == [
        hashlib.sha256(line).hexdigest() for line in _lines(rows)
    ]


def test_records_map_an_individual_with_its_taxonomies_and_addresses(tmp_path: Path) -> None:
    record = next(_dataset(tmp_path, [b.person()]).records(None))

    assert record.npi == b.PERSON_NPI
    assert record.entity_kind is EntityKind.INDIVIDUAL
    assert record.status is NpiStatus.ACTIVE
    assert record.person_name == PersonName(
        last="TESTPERSON", first="AVERY", middle="Q", suffix="JR."
    )
    assert (record.name_prefix, record.credential) == ("DR.", "M.D.")
    assert record.organization_name is None
    assert record.other_names == ("SAMPLEFORD, AVERY",)
    assert record.sole_proprietor is False
    assert record.organization_subpart is None
    assert record.enumeration_date == date(2007, 5, 23)
    assert record.last_updated == date(2025, 7, 8)
    assert record.certification_date == date(2025, 7, 8)
    assert record.mailing_address == Address(
        line1="100 SAMPLE WAY",
        line2="SUITE 2",
        city="SAMPLEVILLE",
        state="NY",
        postal_code="120010001",
        country="US",
    )
    assert record.practice_address == Address(
        line1="200 EXAMPLE ROAD", city="EXAMPLETON", state="NJ", postal_code="07001", country="US"
    )
    assert record.taxonomies == (
        NpiTaxonomy(
            code="207R00000X", primary=True, license_number="SAMPLE-1234", license_state="NY"
        ),
        NpiTaxonomy(
            code="207RC0000X",
            primary=False,
            license_number="XX99",
            license_state="NJ",
            group="193200000X MULTI-SPECIALTY GROUP",
        ),
    )
    assert record.authorized_official is None
    assert record.line_hash == hashlib.sha256(_lines([b.person()])[0]).hexdigest()
    assert record.snapshot_id == SNAPSHOT


def test_records_map_an_organization_with_its_official_and_other_names(tmp_path: Path) -> None:
    record = next(_dataset(tmp_path, [b.organization()]).records(None))

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.person_name is None
    assert record.organization_name == "SAMPLEVILLE CARE GROUP LLC"
    assert record.other_names == ("SAMPLEVILLE CLINIC", "SAMPLEVILLE HEALTH HOLDINGS")
    assert record.organization_subpart is True
    assert record.authorized_official == AuthorizedOfficial(
        name=PersonName(last="EXAMPLE", first="JORDAN"),
        prefix="MS.",
        title="ADMINISTRATOR",
        credential="MBA",
    )
    # A foreign province is not a state code; the country is kept as published.
    assert record.practice_address == Address(
        line1="1 EXAMPLE PLAZA", city="SAMPLE CITY", postal_code="K1A0B1", country="CA"
    )
    assert record.mailing_address is None


def test_a_deactivated_stub_has_only_its_number_and_date(tmp_path: Path) -> None:
    record = next(_dataset(tmp_path, [b.stub()]).records(None))

    assert record.entity_kind is EntityKind.UNKNOWN
    assert record.status is NpiStatus.DEACTIVATED
    assert record.deactivation_date == date(2019, 6, 15)
    assert (record.person_name, record.organization_name, record.taxonomies) == (None, None, ())


def test_a_reactivated_npi_is_active_with_both_dates_and_its_replacement(tmp_path: Path) -> None:
    record = next(_dataset(tmp_path, [b.reactivated()]).records(None))

    assert record.status is NpiStatus.ACTIVE
    assert (record.deactivation_date, record.reactivation_date) == (
        date(2020, 2, 1),
        date(2020, 3, 1),
    )
    assert record.replacement_npi == b.REPLACEMENT_NPI


def test_a_deactivation_after_the_last_reactivation_is_deactivated(tmp_path: Path) -> None:
    row = b.person(**{c.DEACTIVATION_DATE: "04/01/2022", c.REACTIVATION_DATE: "03/01/2020"})

    record = next(_dataset(tmp_path, [row]).records(None))

    assert record.status is NpiStatus.DEACTIVATED
    assert record.entity_kind is EntityKind.INDIVIDUAL


def test_quotes_commas_and_line_breaks_inside_fields_are_kept(tmp_path: Path) -> None:
    rows = [b.quoted(), b.multiline(), b.person()]
    dataset = _dataset(tmp_path, rows)

    quoted, multiline, after = list(dataset.records(None))
    scanned = list(dataset.scan())

    assert quoted.organization_name == 'SAMPLE "CARE", INC'
    assert multiline.mailing_address is not None
    assert multiline.mailing_address.line1 == "100 SAMPLE WAY\nBUILDING B"
    assert after.npi == b.PERSON_NPI
    assert [npi for npi, _ in scanned] == [b.QUOTED_NPI, b.MULTILINE_NPI, b.PERSON_NPI]
    assert [r.line_hash for r in (quoted, multiline, after)] == [h for _, h in scanned]


def test_records_parse_only_the_selected_rows(tmp_path: Path) -> None:
    broken = b.person(make_npi("150000099"), **{c.LAST_UPDATE_DATE: "2025-07-08"})
    dataset = _dataset(tmp_path, [b.person(), broken, b.organization()])

    selected = list(dataset.records({b.ORGANIZATION_NPI, b.PERSON_NPI}))

    assert [record.npi for record in selected] == [b.PERSON_NPI, b.ORGANIZATION_NPI]
    with pytest.raises(DatasetFormatError):
        list(dataset.records(None))


def test_an_unreadable_date_names_the_column_and_line_not_the_value(tmp_path: Path) -> None:
    row = b.person(**{c.ENUMERATION_DATE: "2007-05-23"})

    with pytest.raises(DatasetFormatError) as raised:
        list(_dataset(tmp_path, [b.organization(), row]).records(None))

    assert str(raised.value) == "line 3: Provider Enumeration Date is not a date in MM/DD/YYYY"
    assert "2007" not in str(raised.value)


def test_a_blank_entity_type_without_a_deactivation_date_is_refused(tmp_path: Path) -> None:
    row = b.person(**{c.ENTITY_TYPE: ""})

    with pytest.raises(DatasetFormatError, match="line 2: Entity Type Code"):
        list(_dataset(tmp_path, [row]).records(None))


def test_an_active_individual_without_a_name_does_not_validate(tmp_path: Path) -> None:
    row = b.person(**{c.LAST_NAME: "", c.FIRST_NAME: ""})

    with pytest.raises(
        DatasetFormatError, match=r"line 2: the record does not validate \(record\)"
    ):
        list(_dataset(tmp_path, [row]).records(None))


def test_an_active_organization_without_a_legal_name_is_kept(tmp_path: Path) -> None:
    row = b.organization(**{c.ORGANIZATION_NAME: ""})

    record = next(_dataset(tmp_path, [row]).records(None))

    assert record.organization_name is None
    assert record.status is NpiStatus.ACTIVE


def test_a_header_without_a_needed_column_is_refused_by_name(tmp_path: Path) -> None:
    header = [name for name in c.HEADER if name != c.LAST_UPDATE_DATE]
    body = b.nppes_csv([], header=header)

    with pytest.raises(DatasetFormatError, match="lacks the columns Last Update Date"):
        NppesCsvDataset(_zip(tmp_path, b.nppes_zip(body=body)), SNAPSHOT)


def test_a_quote_that_never_closes_is_refused(tmp_path: Path) -> None:
    body = b.nppes_csv([b.person()]) + f'"{b.ORGANIZATION_NPI}","2","OPEN\n'.encode()
    dataset = NppesCsvDataset(_zip(tmp_path, b.nppes_zip(body=body)), SNAPSHOT)

    with pytest.raises(DatasetFormatError, match="line 3: a quoted field never closes"):
        list(dataset.scan())


def test_a_row_whose_npi_is_not_ten_digits_is_refused(tmp_path: Path) -> None:
    body = b.nppes_csv([b.person(npi="12345")])
    dataset = NppesCsvDataset(_zip(tmp_path, b.nppes_zip(body=body)), SNAPSHOT)

    with pytest.raises(DatasetFormatError, match="line 2: the NPI is not ten digits"):
        list(dataset.scan())


def test_a_zip_without_the_main_csv_is_refused(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", b"nothing here")

    with pytest.raises(DatasetFormatError, match="0 members match"):
        NppesCsvDataset(_zip(tmp_path, buffer.getvalue()), SNAPSHOT)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DatasetFormatError, match="not a zip archive"):
        NppesCsvDataset(_zip(tmp_path, b"Not found."), SNAPSHOT)


def test_close_deletes_the_file_only_when_asked(tmp_path: Path) -> None:
    kept = _zip(tmp_path, b.nppes_zip(), "kept.zip")
    deleted = _zip(tmp_path, b.nppes_zip(), "deleted.zip")

    NppesCsvDataset(kept, SNAPSHOT).close()
    NppesCsvDataset(deleted, SNAPSHOT, delete_on_close=True).close()

    assert kept.exists()
    assert not deleted.exists()


def test_the_deactivation_workbook_yields_stubs_with_a_date_hash(tmp_path: Path) -> None:
    dataset = NppesDeactivationDataset(_zip(tmp_path, b.deactivation_zip()), SNAPSHOT)

    scanned = list(dataset.scan())
    records = list(dataset.records({b.PERSON_NPI}))

    expected = hashlib.sha256(b"deactivated|01/05/2026").hexdigest()
    assert [npi for npi, _ in scanned] == [b.STUB_NPI, b.PERSON_NPI]
    assert scanned[1][1] == expected
    (record,) = records
    assert record.npi == b.PERSON_NPI
    assert record.status is NpiStatus.DEACTIVATED
    assert record.entity_kind is EntityKind.UNKNOWN
    assert record.deactivation_date == date(2026, 1, 5)
    assert record.line_hash == expected


def test_a_deactivation_workbook_without_its_header_is_refused(tmp_path: Path) -> None:
    content = b.deactivation_zip(header=("NPI", "Date"))

    with pytest.raises(DatasetFormatError, match="no header row"):
        NppesDeactivationDataset(_zip(tmp_path, content), SNAPSHOT)


def test_a_deactivation_row_with_a_bad_npi_or_date_names_the_row(tmp_path: Path) -> None:
    bad_npi = b.deactivation_zip(rows=(("12345", "01/05/2026"),))
    bad_date = b.deactivation_zip(rows=((b.STUB_NPI, "01/05/2026"), (b.PERSON_NPI, "Jan 5")))

    with pytest.raises(DatasetFormatError, match="row 3: the NPI is not ten digits"):
        NppesDeactivationDataset(_zip(tmp_path, bad_npi), SNAPSHOT)
    dataset = NppesDeactivationDataset(_zip(tmp_path, bad_date, "date.zip"), SNAPSHOT)
    with pytest.raises(DatasetFormatError, match="row 4: NPPES Deactivation Date is not"):
        list(dataset.scan())


def _page() -> FetchedDocument:
    body = b.files_page()
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=None,
        content_type="text/html",
        final_url="https://download.cms.gov/nppes/NPI_Files.html",
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


@pytest.mark.parametrize(
    ("connector_class", "file_name", "content", "mode"),
    [
        (
            NppesMonthlyConnector,
            "NPPES_Data_Dissemination_January_2026_V2.zip",
            b.nppes_zip(),
            DatasetLoadMode.FULL,
        ),
        (
            NppesWeeklyConnector,
            "NPPES_Data_Dissemination_122925_010426_Weekly_V2.zip",
            b.nppes_zip(member=b.WEEKLY_MEMBER),
            DatasetLoadMode.INCREMENTAL,
        ),
        (
            NppesDeactivationsConnector,
            "NPPES_Deactivated_NPI_Report_010526_V2.zip",
            b.deactivation_zip(),
            DatasetLoadMode.DEACTIVATIONS,
        ),
    ],
)
async def test_each_connector_finds_its_own_link_and_opens_its_file(
    tmp_path: Path,
    connector_class: type[NppesMonthlyConnector],
    file_name: str,
    content: bytes,
    mode: DatasetLoadMode,
) -> None:
    fetcher = ScriptedFetcher(_page(), files=[content])
    connector = connector_class(fetcher, data_dir=tmp_path)

    fetched = await connector.fetch_dataset(None)

    expected_url = f"https://download.cms.gov/nppes/{file_name}"
    assert fetched.snapshot.outcome is Outcome.COMPLETED
    assert fetched.snapshot.source_url == expected_url
    assert fetched.mode is mode
    assert fetcher.requests == [(connector.capabilities.source_url, ChangeSignal.CONTENT_HASH)]
    (download,) = fetcher.downloads
    source_id = connector.capabilities.source_id
    assert download.destination == tmp_path / source_id / file_name
    assert fetched.dataset is not None
    assert len(list(fetched.dataset.scan())) >= 2
    fetched.dataset.close()
    assert not download.destination.exists()
    assert connector.capabilities.credential_type is CredentialType.NPI


async def test_the_weekly_404_is_a_failed_fetch_without_a_dataset(tmp_path: Path) -> None:
    missing = FetchFailure(
        outcome=Outcome.FAILED, status=404, note="unexpected HTTP 404", fetched_at=NOW
    )
    connector = NppesWeeklyConnector(ScriptedFetcher(_page(), files=[missing]), data_dir=tmp_path)

    fetched = await connector.fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.FAILED
    assert fetched.snapshot.note == "unexpected HTTP 404"
    assert fetched.dataset is None
