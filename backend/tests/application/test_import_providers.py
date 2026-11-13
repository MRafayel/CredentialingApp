"""Importing a roster from pasted or exported text, over in-memory storage. Every person is
made up."""

import csv
import io
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.errors import InvalidProviderInputError
from credentialing.application.first_checks import FirstChecks
from credentialing.application.import_providers import (
    HEADER_SYNONYMS,
    MAX_ROWS,
    SEVERITY,
    ImportColumn,
    ImportProblemCode,
    ImportProviders,
    ImportReport,
    ImportRow,
    ImportSeverity,
    header_key,
    split_name,
)
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.provider_status import ProviderStatusKeeper
from credentialing.application.recheck_providers import RecheckProviders
from credentialing.application.register_provider import ProviderInput, RegisterProvider
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import CredentialType, EntityKind, Outcome, RosterRole
from credentialing.domain.models import License, MonitoringRun, Provider, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    API_SOURCE,
    FixedClock,
    InMemoryMonitoringRuns,
    InMemoryNpiRegistry,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
    ScriptedConnector,
    capabilities,
)
from tests.synthetic import NOW, individual, make_npi, npi_person, organization

pytestmark = pytest.mark.anyio

FEDERAL = "fake_federal"
ORG_A, ORG_B = UUID(int=0xA), UUID(int=0xB)
NPI_1, NPI_2, NPI_3 = make_npi("170000001"), make_npi("170000002"), make_npi("170000003")
WRONG_CHECK_DIGIT = NPI_1[:9] + str((int(NPI_1[9]) + 1) % 10)


class _FailingRuns(InMemoryMonitoringRuns):
    """A run store that is down, so the first check cannot finish."""

    async def add_run(self, run: MonitoringRun) -> None:
        raise RuntimeError("synthetic outage")


class _World:
    """One connected list, an NPI mirror knowing ``NPI_1``, and the rosters."""

    def __init__(self, *providers: Provider, runs: InMemoryMonitoringRuns | None = None) -> None:
        self.clock = FixedClock(NOW)
        directory = ConnectorRegistry()
        directory.register(ScriptedConnector(capabilities(FEDERAL)))
        self.snapshots = InMemorySnapshots()
        self.snapshots.stored.append(
            SourceSnapshot(
                id=uuid4(),
                source_id=FEDERAL,
                fetched_at=NOW - timedelta(hours=1),
                outcome=Outcome.COMPLETED,
                source_url="https://example.test/list.csv",
                content_hash="ab" * 32,
            )
        )
        self.providers = InMemoryProviders(*providers)
        self.verifications = InMemoryVerifications()
        self.runs = runs or InMemoryMonitoringRuns()
        keeper = ProviderStatusKeeper(self.providers, self.verifications, directory)
        check = CheckExclusions(
            directory,
            self.providers,
            InMemoryRecords(),
            self.snapshots,
            self.verifications,
            DEFAULT_FRESHNESS,
            self.clock,
            status=keeper,
        )
        lookup = NpiLookup(
            InMemoryNpiRegistry(npi_person(NPI_1)),
            directory,
            self.snapshots,
            DEFAULT_FRESHNESS,
            self.clock,
        )
        verify = VerifyNpi(
            self.providers,
            lookup,
            self.verifications,
            self.clock,
            source_id=API_SOURCE,
            source_url="https://example.test/registry",
            status=keeper,
        )
        recheck = RecheckProviders(
            self.providers,
            check,
            verify,
            keeper,
            self.runs,
            self.clock,
            verifications=self.verifications,
        )
        self.first_checks = FirstChecks(recheck)
        self.importer = ImportProviders(self.providers, keeper, self.first_checks, self.clock)

    async def preview(self, text: str, organization_id: UUID = ORG_A) -> ImportReport:
        return await self.importer.run(organization_id, text, dry_run=True)

    async def commit(self, text: str, organization_id: UUID = ORG_A) -> ImportReport:
        return await self.importer.run(organization_id, text, dry_run=False)


async def _preview(text: str, *roster: Provider) -> ImportReport:
    return await _World(*roster).preview(text)


def _codes(row: ImportRow) -> list[ImportProblemCode]:
    return [problem.code for problem in row.problems]


def _input(row: ImportRow) -> ProviderInput:
    assert row.provider is not None, _codes(row)
    return row.provider


# --- Headers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "column"),
    [
        ("Last Name", ImportColumn.LAST),
        ("LAST_NAME", ImportColumn.LAST),
        ("last-name", ImportColumn.LAST),
        ("Surname", ImportColumn.LAST),
        ("Family Name", ImportColumn.LAST),
        ("lname", ImportColumn.LAST),
        ("First Name", ImportColumn.FIRST),
        ("Given Name", ImportColumn.FIRST),
        ("FNAME", ImportColumn.FIRST),
        ("Middle Name", ImportColumn.MIDDLE),
        ("MI", ImportColumn.MIDDLE),
        ("Suffix", ImportColumn.SUFFIX),
        ("Full Name", ImportColumn.NAME),
        ("Provider", ImportColumn.NAME),
        ("Company", ImportColumn.ORGANIZATION),
        ("Practice", ImportColumn.ORGANIZATION),
        ("Business", ImportColumn.ORGANIZATION),
        ("NPI Number", ImportColumn.NPI),
        ("National Provider Identifier", ImportColumn.NPI),
        ("DOB", ImportColumn.DATE_OF_BIRTH),
        ("Birth Date", ImportColumn.DATE_OF_BIRTH),
        ("Date of Birth", ImportColumn.DATE_OF_BIRTH),
        ("License #", ImportColumn.LICENSE_NUMBER),
        ("Licence", ImportColumn.LICENSE_NUMBER),
        ("Lic", ImportColumn.LICENSE_NUMBER),
        ("License State", ImportColumn.LICENSE_STATE),
        ("State", ImportColumn.LICENSE_STATE),
        ("Type", ImportColumn.ROLE),
        ("Category", ImportColumn.ROLE),
    ],
)
def test_headers_are_recognized_by_their_synonyms(header: str, column: ImportColumn) -> None:
    assert HEADER_SYNONYMS[header_key(header)] is column


def test_every_column_has_a_header_and_every_code_a_severity() -> None:
    assert set(HEADER_SYNONYMS.values()) == set(ImportColumn)
    assert set(SEVERITY) == set(ImportProblemCode)


def test_every_column_is_recognized_by_its_own_name() -> None:
    for column in ImportColumn:
        assert HEADER_SYNONYMS[header_key(column.value)] is column, column


async def test_a_header_of_the_column_names_reads_every_column() -> None:
    report = await _preview("\t".join(ImportColumn) + "\n")

    assert report.columns == tuple(ImportColumn)
    assert report.ignored_columns == ()


async def test_each_row_hands_back_its_recognized_cells_in_file_order() -> None:
    report = await _preview(
        "Employee ID,DOB,Surname,Given Name,Type,NPI\n"
        "E1, 02/03/1970 ,Testperson,Avery,practitionr\n"
        f"E2,,Sampleperson,Blake,staff,{NPI_1}\n"
    )

    first, second = report.rows
    assert list(first.values.items()) == [
        (ImportColumn.DATE_OF_BIRTH, "02/03/1970"),
        (ImportColumn.LAST, "Testperson"),
        (ImportColumn.FIRST, "Avery"),
        (ImportColumn.ROLE, "practitionr"),
        (ImportColumn.NPI, ""),
    ]
    assert _codes(first) == [ImportProblemCode.ROLE_UNRECOGNIZED]
    assert second.values[ImportColumn.DATE_OF_BIRTH] == ""
    assert second.values[ImportColumn.ROLE] == "staff"
    assert second.values[ImportColumn.NPI] == NPI_1


async def test_a_headerless_row_hands_back_its_positional_cells() -> None:
    report = await _preview(f"Testperson\tAvery\t{NPI_1}\nSampleperson\tBlake\n")

    assert [dict(row.values) for row in report.rows] == [
        {ImportColumn.LAST: "Testperson", ImportColumn.FIRST: "Avery", ImportColumn.NPI: NPI_1},
        {ImportColumn.LAST: "Sampleperson", ImportColumn.FIRST: "Blake", ImportColumn.NPI: ""},
    ]


ROUND_TRIP_TEXT = (
    "Surname,Given Name,MI,Suffix,Full Name,Company,NPI Number,DOB,License #,Lic State,Type\n"
    f"Testperson,Avery,J,Jr,,,{NPI_1},02/03/1970,LIC-100,ny,doctor\n"
    ',,,,"Sampleperson, Blake Q",,,1981-12-30,,,practitionr\n'
    f',,,,,"Acme Supply, LLC",{WRONG_CHECK_DIGIT},,,,\n'
    ",,,,,Northwind Staffing,,,555,New York,agency\n"
    f"Testperson,Avery,,,,,{NPI_1},someday,,NY,employee\n"
    "Otherperson,Casey,,,,,,1975-05-06,77,,\n"
    ",Dana,,,,,,,,,\n"
)


def _rebuilt(report: ImportReport) -> str:
    """The preview as the UI rebuilds it: the column names as the header, each row's values,
    tab-separated."""
    lines = io.StringIO()
    writer = csv.writer(lines, csv.excel_tab)
    writer.writerow([column.value for column in report.columns])
    for row in report.rows:
        writer.writerow([row.values[column] for column in report.columns])
    return lines.getvalue()


def _as_previewed(row: ImportRow) -> tuple[object, ...]:
    return (row.line, row.display, row.npi, row.role, row.has_date_of_birth, row.problems)


async def test_a_preview_rebuilt_from_its_columns_and_values_reads_the_same() -> None:
    world = _World(organization("Northwind Staffing").model_copy(update={"organization_id": ORG_A}))
    original = await world.preview(ROUND_TRIP_TEXT)

    again = await world.preview(_rebuilt(original))

    assert original.columns == tuple(ImportColumn)
    assert [_as_previewed(row) for row in again.rows] == [
        _as_previewed(row) for row in original.rows
    ]
    assert [row.values for row in again.rows] == [row.values for row in original.rows]
    assert {code for row in original.rows for code in _codes(row)} == set(ImportProblemCode) - {
        ImportProblemCode.INVALID_ROW
    }


async def test_a_corrected_cell_and_a_dropped_row_clear_their_problems() -> None:
    world = _World()
    original = await world.preview(ROUND_TRIP_TEXT)
    typo = original.rows[1]
    fixed = typo.model_copy(update={"values": {**typo.values, ImportColumn.ROLE: "practitioner"}})
    kept = [fixed, *(row for row in original.rows[2:] if row.line != 3)]

    again = await world.preview(_rebuilt(original.model_copy(update={"rows": (*kept,)})))

    assert (again.rows[0].display, again.rows[0].role, again.rows[0].problems) == (
        "Sampleperson, Blake",
        RosterRole.PRACTITIONER,
        (),
    )
    assert ImportProblemCode.NPI_INVALID not in {c for row in again.rows for c in _codes(row)}


async def test_columns_are_read_in_file_order_and_unknown_ones_are_listed() -> None:
    report = await _preview(
        "Employee ID,Last Name,First Name,NPI,Department,Surname\n"
        f"E1,Testperson,Avery,{NPI_1},Cardiology,Other\n"
    )

    assert report.columns == (ImportColumn.LAST, ImportColumn.FIRST, ImportColumn.NPI)
    assert report.ignored_columns == ("Employee ID", "Department", "Surname")
    assert _input(report.rows[0]).person_name is not None
    assert report.rows[0].display == "Testperson, Avery"


async def test_a_state_column_without_a_licence_number_column_is_ignored() -> None:
    report = await _preview("Last,First,State\nTestperson,Avery,NY\n")

    assert report.columns == (ImportColumn.LAST, ImportColumn.FIRST)
    assert report.ignored_columns == ("State",)
    assert report.rows[0].problems == ()


@pytest.mark.parametrize("delimiter", [",", "\t", ";", "|"])
async def test_the_delimiter_is_found(delimiter: str) -> None:
    lines = [
        ["Last Name", "First Name", "NPI", "DOB"],
        ["Testperson", "Avery", NPI_1, "02/03/1970"],
        ["Sampleperson", "Blake", NPI_2, "1981-12-30"],
    ]
    text = "\n".join(delimiter.join(line) for line in lines) + "\n"

    report = await _preview(text)

    assert report.columns == (
        ImportColumn.LAST,
        ImportColumn.FIRST,
        ImportColumn.NPI,
        ImportColumn.DATE_OF_BIRTH,
    )
    assert [(row.display, row.npi) for row in report.rows] == [
        ("Testperson, Avery", NPI_1),
        ("Sampleperson, Blake", NPI_2),
    ]
    assert report.importable == 2


async def test_a_byte_order_mark_quotes_crlf_and_blank_rows_are_read() -> None:
    text = (
        '﻿Last,First,Organization\r\n"Testperson, Jr",Avery,"Acme, LLC"\r\n,,\r\n'
        "Sampleperson,Blake,\r\n"
    )

    report = await _preview(text)

    assert [row.line for row in report.rows] == [1, 3]
    assert _input(report.rows[0]).person_name is not None
    assert report.rows[0].display == "Testperson, Jr, Avery"


# --- Headerless lists ------------------------------------------------------------------------


async def test_a_headerless_list_reads_last_first_npi() -> None:
    text = f"Testperson\tAvery\t{NPI_1}\nSampleperson\tBlake\n"

    report = await _preview(text)

    assert report.columns == (ImportColumn.LAST, ImportColumn.FIRST, ImportColumn.NPI)
    assert [(row.line, row.display, row.npi) for row in report.rows] == [
        (1, "Testperson, Avery", NPI_1),
        (2, "Sampleperson, Blake", None),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Testperson,Avery\nSampleperson,Blake\n",  # names only: nothing reads as data
        f"Testperson,Avery,J,{NPI_1}\n",  # four cells
        "Accounts payable\n",
    ],
)
async def test_text_without_a_header_or_a_headerless_shape_reads_nothing(text: str) -> None:
    report = await _preview(text)

    assert report.no_header
    assert report.rows == ()


async def test_an_empty_text_reads_nothing() -> None:
    report = await _preview("  \n\n")

    assert report == ImportReport()


# --- Names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("whole", "parts"),
    [
        ("Testperson, Avery", ("Testperson", "Avery", None, None)),
        ("Testperson, Avery J Q", ("Testperson", "Avery", "J Q", None)),
        ("Avery Testperson", ("Testperson", "Avery", None, None)),
        ("Avery J Testperson", ("Testperson", "Avery", "J", None)),
        ("Avery Testperson Jr.", ("Testperson", "Avery", None, "Jr")),
        ("Avery Testperson MD", ("Testperson", "Avery", None, None)),
        ("Testperson, Avery M.D.", ("Testperson", "Avery", None, None)),
        ("Testperson", ("Testperson", None, None, None)),
        ("Testperson,", ("Testperson", None, None, None)),
    ],
)
def test_a_whole_name_is_split(
    whole: str, parts: tuple[str | None, str | None, str | None, str | None]
) -> None:
    assert split_name(whole) == parts


async def test_a_name_column_is_split_and_the_row_says_so() -> None:
    report = await _preview(f'Name,NPI\n"Testperson, Avery J",{NPI_1}\nBlake Sampleperson,\n')

    first, second = report.rows
    assert (first.name_split, second.name_split) == (True, True)
    name = _input(first).person_name
    assert name is not None
    assert (name.last, name.first, name.middle) == ("Testperson", "Avery", "J")
    assert second.display == "Sampleperson, Blake"


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("Last,First,NPI\n,Avery,\n", "last name"),
        ("Last,First,NPI\nTestperson,,\n", "first name"),
        (f"Last,First,NPI\n,,{NPI_1}\n", "last and first name"),
        ("Name,NPI\nTestperson,\n", "first name"),
    ],
)
async def test_an_individual_needs_a_last_and_a_first_name(text: str, detail: str) -> None:
    (row,) = (await _preview(text)).rows

    assert _codes(row) == [ImportProblemCode.NAME_MISSING]
    assert row.problems[0].severity is ImportSeverity.ERROR
    assert row.problems[0].detail == detail
    assert row.provider is None


# --- Entity kind and role ------------------------------------------------------------------


async def test_an_organization_row_and_an_employer_column() -> None:
    report = await _preview(
        "Last,First,Organization\n,,Sampleville Care Group\nTestperson,Avery,Sampleville Clinic\n"
    )

    party, person = report.rows
    assert party.entity_kind is EntityKind.ORGANIZATION
    assert _input(party).organization_name == "Sampleville Care Group"
    assert party.display == "Sampleville Care Group"
    assert party.role is RosterRole.VENDOR
    assert person.entity_kind is EntityKind.INDIVIDUAL
    assert _input(person).organization_name is None
    assert person.role is RosterRole.WORKFORCE


@pytest.mark.parametrize(
    ("cell", "role"),
    [
        ("Provider", RosterRole.PRACTITIONER),
        ("M.D.", RosterRole.PRACTITIONER),
        ("np", RosterRole.PRACTITIONER),
        ("Clinician", RosterRole.PRACTITIONER),
        ("Employee", RosterRole.WORKFORCE),
        ("Volunteer", RosterRole.WORKFORCE),
        ("manager", RosterRole.WORKFORCE),
        ("Contractor", RosterRole.VENDOR),
        ("Agency", RosterRole.VENDOR),
        ("vendor", RosterRole.VENDOR),
    ],
)
async def test_roles_are_read_by_their_synonyms(cell: str, role: RosterRole) -> None:
    (row,) = (await _preview(f"Last,First,Role\nTestperson,Avery,{cell}\n")).rows

    assert row.role is role
    assert _input(row).role is role
    assert row.problems == ()


async def test_a_blank_or_unknown_role_follows_the_npi() -> None:
    report = await _preview(
        f"Last,First,NPI,Role\nTestperson,Avery,{NPI_1},\nSampleperson,Blake,,\n"
        f"Exampleperson,Casey,{NPI_2},Astronaut\n"
    )

    with_npi, without, unknown = report.rows
    assert with_npi.role is RosterRole.PRACTITIONER
    assert without.role is RosterRole.WORKFORCE
    assert unknown.role is RosterRole.PRACTITIONER
    assert _codes(unknown) == [ImportProblemCode.ROLE_UNRECOGNIZED]
    assert unknown.problems[0].detail == "practitioner, workforce or vendor"
    assert unknown.provider is not None
    assert report.with_warnings == 1


# --- Values --------------------------------------------------------------------------------


@pytest.mark.parametrize("cell", ["1970-02-03", "02/03/1970", "2/3/1970", "02-03-1970"])
async def test_dates_of_birth_are_read(cell: str) -> None:
    (row,) = (await _preview(f"Last,First,DOB\nTestperson,Avery,{cell}\n")).rows

    assert _input(row).date_of_birth == date(1970, 2, 3)
    assert row.has_date_of_birth
    assert row.problems == ()


@pytest.mark.parametrize("cell", ["1970/02/03", "02/30/1970", "3 Feb 1970", "01/01/2099"])
async def test_an_unreadable_date_of_birth_is_dropped_with_a_warning(cell: str) -> None:
    (row,) = (await _preview(f"Last,First,DOB\nTestperson,Avery,{cell}\n")).rows

    assert _codes(row) == [ImportProblemCode.DATE_OF_BIRTH_UNREADABLE]
    assert row.problems[0].severity is ImportSeverity.WARNING
    assert _input(row).date_of_birth is None
    assert not row.has_date_of_birth


@pytest.mark.parametrize(
    "cell", [NPI_1, f"{NPI_1[:4]}-{NPI_1[4:7]}-{NPI_1[7:]}", f" {NPI_1[:5]} {NPI_1[5:]} "]
)
async def test_an_npi_is_cleaned_of_spaces_and_dashes(cell: str) -> None:
    (row,) = (await _preview(f"Last\tFirst\tNPI\nTestperson\tAvery\t{cell}\n")).rows

    assert (row.npi, _input(row).npi, row.problems) == (NPI_1, NPI_1, ())


@pytest.mark.parametrize("cell", [WRONG_CHECK_DIGIT, "12345", "170000001X"])
async def test_an_invalid_npi_keeps_the_row_out(cell: str) -> None:
    report = await _preview(f"Last,First,NPI\nTestperson,Avery,{cell}\n")

    (row,) = report.rows
    assert _codes(row) == [ImportProblemCode.NPI_INVALID]
    assert row.provider is None
    assert (report.importable, report.rejected) == (0, 1)


@pytest.mark.parametrize("cell", ["N/A", "none", "-"])
async def test_an_npi_cell_without_digits_is_blank(cell: str) -> None:
    (row,) = (await _preview(f"Last,First,NPI\nTestperson,Avery,{cell}\n")).rows

    assert (row.npi, row.problems) == (None, ())


async def test_a_licence_needs_a_two_letter_state_and_may_have_no_number() -> None:
    report = await _preview(
        "Last,First,License Number,License State\n"
        "Testperson,Avery,0012345,ny\n"
        "Sampleperson,Blake,0012346,New York\n"
        "Exampleperson,Casey,0012347,\n"
        "Otherperson,Dana,,nj\n"
    )

    full, long_state, no_state, state_only = report.rows
    assert _input(full).licenses == (License(number="0012345", state="NY"),)
    assert _codes(long_state) == [ImportProblemCode.LICENSE_STATE_INVALID]
    assert _codes(no_state) == [ImportProblemCode.LICENSE_INCOMPLETE]
    assert [problem.detail for problem in no_state.problems] == ["no licence state"]
    for row in (long_state, no_state):
        assert _input(row).licenses == ()
    assert (_codes(state_only), _input(state_only).licenses) == ([], (License(state="NJ"),))
    assert (report.importable, report.with_warnings, report.rejected) == (4, 2, 0)


async def test_a_licence_state_column_alone_holds_licence_states() -> None:
    report = await _preview("Last,First,License State\nTestperson,Avery,NY\nSampleperson,Blake,\n")

    assert report.columns == (ImportColumn.LAST, ImportColumn.FIRST, ImportColumn.LICENSE_STATE)
    assert report.ignored_columns == ()
    licensed, unlicensed = report.rows
    assert (_codes(licensed), _input(licensed).licenses) == ([], (License(state="NY"),))
    assert (_codes(unlicensed), _input(unlicensed).licenses) == ([], ())


# --- Duplicates ----------------------------------------------------------------------------


async def test_duplicates_in_the_file_by_npi_and_by_name_and_date_of_birth() -> None:
    report = await _preview(
        "Last,First,NPI,DOB\n"
        f"Testperson,Avery,{NPI_1},\n"
        f"Otherperson,Dana,{NPI_1},\n"
        "Sampleperson,Blake,,1970-02-03\n"
        "SAMPLEPERSON,blake,,02/03/1970\n"
        "Sampleperson,Blake,,1971-02-03\n"
    )

    lines = [(row.line, row.duplicate_of_line, _codes(row)) for row in report.rows]
    assert lines == [
        (1, None, []),
        (2, 1, [ImportProblemCode.DUPLICATE_IN_FILE]),
        (3, None, []),
        (4, 3, [ImportProblemCode.DUPLICATE_IN_FILE]),
        (5, None, []),
    ]
    assert (report.importable, report.rejected) == (3, 2)


async def test_a_rejected_row_is_never_the_original_of_a_duplicate() -> None:
    report = await _preview(f"Last,First,NPI\n,Avery,{NPI_1}\nTestperson,Avery,{NPI_1}\n")

    rejected, kept = report.rows
    assert _codes(rejected) == [ImportProblemCode.NAME_MISSING]
    assert kept.problems == ()


async def test_rows_already_on_the_callers_roster_are_refused() -> None:
    by_npi = individual(last="Rosterperson", npi=NPI_1, organization_id=ORG_A)
    by_name = individual(
        last="Sampleperson", first="Blake", date_of_birth=date(1970, 2, 3), organization_id=ORG_A
    )
    party = organization("Sampleville Care Group").model_copy(update={"organization_id": ORG_A})
    report = await _preview(
        "Last,First,NPI,DOB,Organization\n"
        f"Testperson,Avery,{NPI_1},,\n"
        "Sampleperson,Blake,,,\n"
        "Sampleperson,Blake,,1981-01-01,\n"
        ",,,,The Sampleville Care Group LLC\n",
        by_npi,
        by_name,
        party,
    )

    same_npi, same_name, other_birthday, same_party = report.rows
    assert (same_npi.existing_provider_id, _codes(same_npi)) == (
        by_npi.id,
        [ImportProblemCode.ALREADY_ON_ROSTER],
    )
    assert same_name.existing_provider_id == by_name.id
    assert other_birthday.problems == ()
    assert same_party.existing_provider_id == party.id


async def test_every_problem_code_has_its_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_one(organization_id: UUID, data: ProviderInput, now: datetime) -> Provider:
        if data.person_name is not None and data.person_name.last == "Refusedperson":
            raise InvalidProviderInputError("provider: synthetic rule")
        return original(organization_id, data, now)

    original = RegisterProvider.build
    monkeypatch.setattr(RegisterProvider, "build", staticmethod(refuse_one))
    roster = individual(last="Rosterperson", npi=NPI_3, organization_id=ORG_A)
    report = await _preview(
        "Last,First,NPI,DOB,License,State,Role\n"
        f"Testperson,Avery,{WRONG_CHECK_DIGIT},,,,\n"
        ",Blake,,,,,\n"
        f"Sampleperson,Casey,{NPI_2},,,,\n"
        f"Sampleperson,Casey,{NPI_2},,,,\n"
        f"Rosterperson,Dana,{NPI_3},,,,\n"
        "Refusedperson,Eli,,,,,\n"
        "Datesperson,Finn,,someday,,,\n"
        "Stateperson,Gale,,,123,New York,\n"
        "Halfperson,Hana,,,123,,\n"
        "Roleperson,Ira,,,,,Astronaut\n",
        roster,
    )

    seen = {problem.code: problem.severity for row in report.rows for problem in row.problems}
    assert seen == SEVERITY
    assert (report.importable, report.with_warnings, report.rejected) == (5, 4, 5)


# --- Limits --------------------------------------------------------------------------------


async def test_more_than_the_row_limit_is_refused() -> None:
    rows = "".join(f"Person{index},Avery\n" for index in range(MAX_ROWS + 1))

    with pytest.raises(InvalidProviderInputError, match="more than 5,000 rows"):
        await _preview("Last,First\n" + rows)


async def test_more_than_two_megabytes_is_refused() -> None:
    with pytest.raises(InvalidProviderInputError, match="larger than 2 MB"):
        await _preview("Last,First\n" + "x" * (2 * 1024 * 1024))


async def test_text_that_is_not_delimited_text_is_refused() -> None:
    with pytest.raises(InvalidProviderInputError, match="could not be read"):
        await _preview("Last,First\nTestperson," + "y" * 200_000 + "\n")


# --- Commit --------------------------------------------------------------------------------

TEXT = (
    "Last,First,NPI\n"
    f"Testperson,Avery,{NPI_1}\n"
    "Sampleperson,Blake,\n"
    f"Badperson,Casey,{WRONG_CHECK_DIGIT}\n"
)


async def test_a_dry_run_stores_nothing_and_checks_nothing() -> None:
    world = _World()

    report = await world.preview(TEXT)

    assert (report.importable, report.rejected, report.imported, report.checking) == (
        2,
        1,
        (),
        0,
    )
    assert world.providers.stored == {}
    assert world.runs.stored == []
    assert world.verifications.stored == []
    assert world.first_checks.running == 0


async def test_a_commit_stores_the_importable_rows_and_checks_exactly_them() -> None:
    world = _World()

    report = await world.commit(TEXT)

    assert len(report.imported) == 2
    stored = [world.providers.stored[provider_id] for provider_id in report.imported]
    assert all(provider.organization_id == ORG_A for provider in stored)
    assert [p.person_name.last for p in stored if p.person_name] == ["Testperson", "Sampleperson"]
    assert stored[0].created_at < stored[1].created_at  # file order is kept
    assert stored[0].role is RosterRole.PRACTITIONER
    assert stored[1].role is RosterRole.WORKFORCE
    assert report.checking == 2
    # The answer came before the check: statuses stored, nothing checked, the check waiting.
    statuses = [status for _, status in await world.providers.list_all(ORG_A)]
    assert all(status is not None and status.last_checked_at is None for status in statuses)
    assert world.verifications.stored == []
    assert world.first_checks.waiting(ORG_A) == set(report.imported)
    await world.first_checks.wait()
    (run,) = world.runs.stored
    assert (run.trigger, run.organizations, run.providers, run.failures) == ("import", 1, 2, 0)
    assert run.results == 3  # the list for both, the NPI for the one with an NPI
    assert world.first_checks.waiting(ORG_A) == frozenset()
    checked = {(r.provider_id, r.credential_type) for r in world.verifications.stored}
    assert checked == {
        (stored[0].id, CredentialType.EXCLUSION),
        (stored[0].id, CredentialType.NPI),
        (stored[1].id, CredentialType.EXCLUSION),
    }
    statuses = [status for _, status in await world.providers.list_all(ORG_A)]
    assert all(status is not None and status.last_checked_at == NOW for status in statuses)


async def test_another_organization_is_never_consulted_or_touched() -> None:
    theirs = individual(last="Testperson", npi=NPI_1, organization_id=ORG_B)
    twin = individual(last="Sampleperson", first="Blake", organization_id=ORG_B)
    world = _World(theirs, twin)

    report = await world.commit(TEXT)
    await world.first_checks.wait()

    assert [row.existing_provider_id for row in report.rows] == [None, None, None]
    assert len(report.imported) == 2
    assert {r.organization_id for r in world.verifications.stored} == {ORG_A}
    assert world.providers.stored[theirs.id] == theirs
    assert {p.id for p, _ in await world.providers.list_all(ORG_B)} == {theirs.id, twin.id}
    assert world.providers.stored[twin.id] == twin


async def test_a_commit_with_nothing_importable_stores_and_checks_nothing() -> None:
    world = _World()

    report = await world.commit(f"Last,First,NPI\nTestperson,Avery,{WRONG_CHECK_DIGIT}\n")

    assert (report.imported, report.checking) == ((), 0)
    assert world.providers.stored == {}
    assert world.runs.stored == []
    assert world.first_checks.running == 0


async def test_the_import_stands_when_the_first_check_cannot_run() -> None:
    world = _World(runs=_FailingRuns())

    report = await world.commit(TEXT)
    await world.first_checks.wait()

    assert (len(report.imported), report.checking) == (2, 2)
    assert set(world.providers.stored) == set(report.imported)
    assert world.first_checks.waiting(ORG_A) == frozenset()  # the import stands, unchecked
