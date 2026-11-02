from datetime import date, timedelta
from uuid import uuid4

import pytest

from credentialing.connectors.framework.rows import ParsedList
from credentialing.connectors.wy_wdh import WyWdhConnector
from credentialing.connectors.wy_wdh.connector import (
    CAPABILITIES,
    LANDING_URL,
    ProviderNumbers,
    parse_provider_numbers,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    EntityKind,
    ExclusionAction,
    Outcome,
)
from credentialing.domain.models import (
    Address,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)
from tests.connectors.fixtures import builders, fixture_bytes, wy_builders
from tests.fakes import ScriptedFetcher
from tests.synthetic import NOW

SNAPSHOT = SourceSnapshot(
    id=uuid4(),
    source_id="wy_wdh",
    fetched_at=NOW,
    outcome=Outcome.COMPLETED,
    source_url="https://health.wyo.gov/wp-content/uploads/2026/08/x-Exclusion-List.pdf",
)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return WyWdhConnector(ScriptedFetcher()).parse(wy_builders.wy_wdh(), SNAPSHOT)


def _record(parsed: ParsedList, last_name: str) -> ExclusionRecord:
    [record] = [r for r in parsed.records if r.raw["Last Name"] == last_name]
    return record


def test_counts(parsed: ParsedList) -> None:
    assert parsed.rows == 18
    assert parsed.skipped == 1
    assert parsed.merged == 1
    assert len(parsed.records) == 16
    assert parsed.unreadable == {}
    assert all(r.action is ExclusionAction.EXCLUSION for r in parsed.records)


def test_a_row_with_only_a_business_name_is_an_organization(parsed: ParsedList) -> None:
    record = _record(parsed, "")

    assert record.entity_kind is EntityKind.ORGANIZATION
    assert record.organization_name == "Samplewood's Home Care"
    assert record.state_provider_id == "120000100"
    assert record.address == Address(city="Casper", state="WY")
    assert record.effective_date == date(2022, 6, 23)
    assert record.raw["Provider Type"] == "Case Management\nWaiver"


@pytest.mark.parametrize(
    ("last_name", "person", "alias"),
    [
        (
            "Tester (Maidmock)",
            PersonName(last="Tester", first="Avery", middle="Quinn"),
            "Maidmock, Avery Quinn",
        ),
        (
            "(Formermock) Sampleton",
            PersonName(last="Sampleton", first="Jordan"),
            "Formermock, Jordan",
        ),
        (
            "Mockwell(Priorname)",
            PersonName(last="Mockwell", first="Riley", middle="Dawn"),
            "Priorname, Riley Dawn",
        ),
    ],
)
def test_a_former_name_in_the_last_name_is_an_alias_in_every_position(
    parsed: ParsedList, last_name: str, person: PersonName, alias: str
) -> None:
    record = _record(parsed, last_name)

    assert record.person_name == person
    assert record.aliases == (alias,)


def test_a_bare_number_is_no_licence(parsed: ParsedList) -> None:
    record = _record(parsed, "Tester (Maidmock)")

    assert record.license is None
    assert record.state_provider_id is None
    assert record.raw["Provider Number"] == "20001"


def test_rows_differing_only_in_business_name_are_one_record(parsed: ParsedList) -> None:
    record = _record(parsed, "Exampleton")

    assert record.person_name == PersonName(last="Exampleton", first="Bartholomew")
    assert record.aliases == ("Exampleton, Bart",)
    assert record.related_parties == (
        "Exampleton Mock Ventures",
        "Sampleville Support Center (SSC)",
    )
    assert (
        record.raw["Business Name"]
        == "Exampleton Mock\nVentures; Sampleville Support\nCenter (SSC)"
    )
    assert record.state_provider_id == "127000700"


def test_a_marked_business_name_is_an_alias_and_a_wrapped_licence_is_joined(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "Testwell")

    assert record.aliases == ("Priorwell, Morgan E",)
    assert record.related_parties == ()
    assert record.license == License(number="1234567-1234", state="UT")
    assert record.address == Address(city="Ogden", state="UT")


def test_an_npi_and_a_spaced_medicaid_id(parsed: ParsedList) -> None:
    record = _record(parsed, "Mockridge")

    assert record.npi == builders.NPI_A
    assert record.state_provider_id == "123456700"
    assert record.related_parties == ("Sample Breathing Supply",)


def test_the_first_of_several_licences_and_another_states_nine_digits(
    parsed: ParsedList,
) -> None:
    record = _record(parsed, "Nursemock")

    assert record.license == License(number="31001", state="WY", kind="RN")
    assert record.state_provider_id is None
    assert record.address == Address(city="Denver", state="CO")


def test_a_licence_in_the_provider_type_when_the_number_is_na(parsed: ParsedList) -> None:
    record = _record(parsed, "Aidemock")

    assert record.license == License(number="20003", state="WY")


def test_an_unmarked_business_is_a_related_party(parsed: ParsedList) -> None:
    clinic = _record(parsed, "Doctormock")
    own = _record(parsed, "Ownermock")

    assert clinic.related_parties == ("Example Pines Clinic",)
    assert clinic.aliases == ()
    assert clinic.state_provider_id == "111000002"
    assert own.related_parties == ("Alex Ownermock Nursing Services, Inc.",)
    assert own.npi == builders.NPI_B


def test_a_suffix_after_the_last_name(parsed: ParsedList) -> None:
    record = _record(parsed, "Suffixmock JR")

    assert record.person_name == PersonName(
        last="Suffixmock", first="Lee", middle="Allen", suffix="JR"
    )
    assert record.npi == builders.NPI_C


def test_other_names_in_the_first_name(parsed: ParsedList) -> None:
    surnames = _record(parsed, "Quinnmock")
    full_name = _record(parsed, "Altmock")

    assert surnames.person_name == PersonName(last="Quinnmock", first="Dana", middle="Lynn")
    assert surnames.aliases == ("Oldname, Dana Lynn", "Othername, Dana Lynn")
    assert full_name.person_name == PersonName(last="Altmock", first="Casey", middle="Jo")
    assert full_name.aliases == ("Casey Formerton",)


def test_a_marked_list_of_surnames_in_the_business_name(parsed: ParsedList) -> None:
    record = _record(parsed, "Listmock")

    assert record.aliases == (
        "Onemock, Robin Kay",
        "Twomock, Robin Kay",
        "Threemock, Robin Kay",
    )
    assert record.license == License(number="3001", state="WY", kind="CNA")


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("N/A", ProviderNumbers(npis=(), state_provider_id=None, license=None)),
        ("120000200", ProviderNumbers(npis=(), state_provider_id="120000200", license=None)),
        (
            f"1000001 00 WY\nProv # NPI\n{builders.NPI_A}",
            ProviderNumbers(npis=(builders.NPI_A,), state_provider_id="100000100", license=None),
        ),
        (
            f"NPI {builders.NPI_A}; WY Provider ID 1234567 00",
            ProviderNumbers(npis=(builders.NPI_A,), state_provider_id="123456700", license=None),
        ),
        (
            "RN 31001 (WY) RN 012345678 (OR)",
            ProviderNumbers(
                npis=(),
                state_provider_id=None,
                license=License(number="31001", state="WY", kind="RN"),
            ),
        ),
        (
            "100000400\nWYLicense#1234A",
            ProviderNumbers(
                npis=(), state_provider_id="100000400", license=License(number="1234A", state="WY")
            ),
        ),
        ("WYRN# 10001", ProviderNumbers((), None, License(number="10001", state="WY", kind="RN"))),
        ("RN123456 CO", ProviderNumbers((), None, License(number="123456", state="CO", kind="RN"))),
        ("LPN6001", ProviderNumbers((), None, License(number="6001", state="WY", kind="LPN"))),
        ("CNA #20005", ProviderNumbers((), None, License(number="20005", state="WY", kind="CNA"))),
        ("License #WY10002", ProviderNumbers((), None, License(number="10002", state="WY"))),
        (
            "RN SD LICENSE\n9001",
            ProviderNumbers((), None, License(number="9001", state="SD", kind="RN")),
        ),
        (
            "RN.0100001 (CO)",
            ProviderNumbers((), None, License(number="0100001", state="CO", kind="RN")),
        ),
        (
            "WY-RN: 30001\nCNP: 1001\nNV-RN:60001",
            ProviderNumbers((), None, License(number="30001", state="WY", kind="RN")),
        ),
        ("NV-RN:60001", ProviderNumbers((), None, License(number="60001", state="NV", kind="RN"))),
        ("WY Lic 20006", ProviderNumbers((), None, License(number="20006", state="WY"))),
        ("WY20002,", ProviderNumbers(npis=(), state_provider_id=None, license=None)),
        ("1234A(WY), 30002 (AZ)", ProviderNumbers(npis=(), state_provider_id=None, license=None)),
        ("20001", ProviderNumbers(npis=(), state_provider_id=None, license=None)),
    ],
)
def test_provider_number_patterns(number: str, expected: ProviderNumbers) -> None:
    assert parse_provider_numbers(number) == expected


def test_capabilities_honour_the_crawl_delay_and_discover_the_link() -> None:
    assert CAPABILITIES.change_signal is ChangeSignal.LINK_DISCOVERY
    assert CAPABILITIES.download_url is None
    assert CAPABILITIES.crawl_delay == timedelta(seconds=10)


def test_a_landing_page_cloudflare_refuses_is_repeated_in_the_browser() -> None:
    assert CAPABILITIES.browser_fallback is True
    assert CAPABILITIES.access_policy is AccessPolicy.HTTP
    assert WyWdhConnector(ScriptedFetcher(), browser=ScriptedFetcher()).uses_browser
    assert CAPABILITIES.provider_type_fields == ("Provider Type",)
    assert WyWdhConnector.landing_url == LANDING_URL


def test_the_list_link_is_found_among_other_pdfs() -> None:
    page = fixture_bytes("wy_wdh_landing.html").decode()

    link = WyWdhConnector(ScriptedFetcher()).find_link(page)

    assert link == (
        "https://health.wyo.gov/wp-content/uploads/2026/08/"
        "2026_08_01_-Wyoming-Medicaid-Exclusion-List.pdf"
    )
