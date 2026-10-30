from collections.abc import Mapping
from datetime import date
from uuid import uuid4

import pytest

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    UnreadableRow,
    build_records,
    merge_duplicates,
)
from credentialing.domain.enums import EntityKind, ExclusionAction
from credentialing.domain.models import Address, ExclusionRecord, PersonName
from tests.synthetic import NOW, exclusion, make_npi


def _build(row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    return exclusion()


def test_unreadable_rows_are_counted_as_skipped() -> None:
    parsed = build_records([{"NAME": "A"}, UnreadableRow("line 2"), {"NAME": "B"}], _build)

    assert (parsed.rows, len(parsed.records), parsed.skipped) == (3, 2, 1)


def test_a_file_of_unreadable_rows_is_rejected() -> None:
    with pytest.raises(ParseError, match="no usable rows"):
        build_records([UnreadableRow("line 1")], _build)
    with pytest.raises(ParseError, match="11 of 12 rows unusable"):
        build_records([{"NAME": "A"}, *[UnreadableRow("line") for _ in range(11)]], _build)


TYPE, ADDRESSES, ALIASES = "Provider Type", "Addresses", "Aliases"
JOINED = (TYPE, ADDRESSES, ALIASES)


def _row(provider_type: str, addresses: str = "", aliases: str = "") -> dict[str, str]:
    return {"Last": "Testperson", TYPE: provider_type, ADDRESSES: addresses, ALIASES: aliases}


def _with_address(
    address: Address,
    *,
    aliases: tuple[str, ...],
    related_parties: tuple[str, ...],
    related_npis: tuple[str, ...],
    raw: Mapping[str, str],
) -> ExclusionRecord:
    """A made-up record of the identity ``exclusion(npi=make_npi("100000007"))`` has."""
    return ExclusionRecord.create(
        id=uuid4(),
        source_id="test_exclusions",
        snapshot_id=uuid4(),
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Testperson", first="Avery"),
        npi=make_npi("100000007"),
        effective_date=date(2024, 3, 1),
        action=ExclusionAction.EXCLUSION,
        address=address,
        aliases=aliases,
        related_parties=related_parties,
        related_npis=related_npis,
        raw=raw,
        valid_from=NOW,
    )


def test_records_sharing_an_identity_are_merged() -> None:
    first = exclusion(
        npi=make_npi("100000007"),
        aliases=("Testperson, A.",),
        related_npis=(make_npi("100000008"),),
        raw=_row("Physician", "Sampleville, CA", "Testperson, A."),
    )
    second = _with_address(
        Address(city="Testtown", state="CA"),
        aliases=("Testperson, A.", "Maidenmock, Avery"),
        related_parties=("Sample Clinic, Inc.",),
        related_npis=(make_npi("100000008"), make_npi("100000009")),
        raw=_row("Surgeon", "  Testtown,\nCA", "Maidenmock, Avery"),
    )
    third = exclusion(npi=make_npi("100000007"), raw=_row("physician", "Sampleville,  CA"))

    [merged] = merge_duplicates([first, second, third], joined=JOINED)

    assert merged.record_hash == first.record_hash
    assert merged.id == first.id
    assert merged.raw == {
        "Last": "Testperson",
        TYPE: "Physician; Surgeon; physician",
        ADDRESSES: "Sampleville, CA;   Testtown,\nCA",
        ALIASES: "Testperson, A.; Maidenmock, Avery",
    }
    assert merged.aliases == ("Testperson, A.", "Maidenmock, Avery")
    assert merged.related_parties == ("Sample Clinic, Inc.",)
    assert merged.related_npis == (make_npi("100000008"), make_npi("100000009"))
    assert merged.address == Address(city="Testtown", state="CA")
    assert merged.content_hash not in {first.content_hash, second.content_hash}


def test_a_different_identifying_field_is_never_merged() -> None:
    base = exclusion(npi=make_npi("100000007"), raw=_row("Physician"))
    other_npi = exclusion(npi=make_npi("100000008"), raw=_row("Surgeon"))
    other_date = exclusion(
        npi=make_npi("100000007"), effective_date=date(2020, 1, 2), raw=_row("Nurse")
    )
    repeat = exclusion(npi=make_npi("100000007"), raw=_row("Pharmacist"))

    records = merge_duplicates([base, other_npi, other_date, repeat], joined=JOINED)

    assert [r.raw[TYPE] for r in records] == ["Physician; Pharmacist", "Surgeon", "Nurse"]
    assert records[1] is other_npi
    assert records[2] is other_date


def test_single_records_and_equal_joined_values_stay_as_they_are() -> None:
    single = exclusion(last="Sampleperson", raw=_row("Physician"))
    first = exclusion(raw=_row("Nurse", "Testtown, CA"))
    repeat = exclusion(raw=_row("Nurse", "Testtown,  CA"))

    records = merge_duplicates([single, first, repeat], joined=JOINED)

    assert records[0] is single
    assert records[1].raw == first.raw
    assert records[1].content_hash == first.content_hash


def test_related_npis_union_leaves_out_the_records_own_npi() -> None:
    own = make_npi("100000007")
    first = exclusion(npi=own, raw=_row("Physician"))
    second = exclusion(npi=own, related_npis=(own, make_npi("100000008")), raw=_row("Physician"))

    [merged] = merge_duplicates([first, second], joined=JOINED)

    assert merged.npi == own
    assert merged.related_npis == (make_npi("100000008"),)


def test_the_note_counts_merged_rows() -> None:
    parsed = ParsedList(records=(exclusion(),), rows=3, merged=2, unreadable={"DATE": 1})

    assert parsed.note() == "3 rows, 1 records, 0 skipped; 2 merged; unreadable values: DATE 1"
    assert "merged" not in ParsedList(records=(exclusion(),), rows=1).note()
