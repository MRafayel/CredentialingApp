from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from credentialing.domain.enums import DatasetLoadMode, EntityKind, NpiStatus, Outcome
from credentialing.domain.models import (
    Address,
    DatasetLoad,
    NpiRecord,
    PersonName,
    SourceSnapshot,
    compute_content_hash,
)
from credentialing.domain.ports import DatasetFetch
from tests.fakes import ListDataset
from tests.synthetic import NOW, make_npi

NPI = make_npi("160000001")
SNAPSHOT = uuid4()


def _content_hash(address: Address) -> str:
    return compute_content_hash(
        end_date=None,
        reason="Fraud",
        action_raw="1128a1",
        aliases=("Sample Alias",),
        date_of_birth=date(1970, 1, 2),
        address=address,
        state_provider_id=None,
        license=None,
        raw={"NAME": "Testperson"},
    )


def test_an_address_without_a_country_hashes_as_before_the_field_existed() -> None:
    address = Address(line1="1 Sample Way", city="Sampleville", state="NY", postal_code="12345")

    # Captured before ``Address.country`` was added: stored exclusion rows keep their hash.
    assert _content_hash(address) == (
        "c8723aef82229050f839cdc6ad743082e5777455c0e1cd57d009143037c2801a"
    )
    assert _content_hash(address.model_copy(update={"country": "US"})) != _content_hash(address)


def _record(**fields: object) -> NpiRecord:
    values: dict[str, object] = {
        "npi": NPI,
        "entity_kind": EntityKind.INDIVIDUAL,
        "status": NpiStatus.ACTIVE,
        "person_name": PersonName(last="Testperson", first="Avery"),
        "line_hash": "ab" * 32,
        "snapshot_id": SNAPSHOT,
    }
    values.update(fields)
    return NpiRecord.model_validate(values)


def test_a_deactivated_stub_needs_nothing_but_its_number() -> None:
    stub = _record(
        entity_kind=EntityKind.UNKNOWN,
        status=NpiStatus.DEACTIVATED,
        person_name=None,
        deactivation_date=date(2019, 6, 15),
    )

    assert stub.in_file is True
    assert stub.other_names == ()


@pytest.mark.parametrize(
    "fields",
    [
        {"entity_kind": EntityKind.UNKNOWN, "person_name": None},
        {"person_name": None},
        {"organization_name": "Sampleville Care Group"},
        {"entity_kind": EntityKind.ORGANIZATION, "organization_name": "Sampleville Care Group"},
        {"npi": "12345"},
        {"line_hash": ""},
    ],
)
def test_records_that_break_the_registry_rules_do_not_validate(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _record(**fields)


def test_an_organization_may_lack_its_legal_name_and_a_deactivated_person_a_name() -> None:
    organization = _record(entity_kind=EntityKind.ORGANIZATION, person_name=None)
    person = _record(person_name=None, status=NpiStatus.DEACTIVATED)

    assert organization.organization_name is None
    assert person.person_name is None


def test_a_load_reports_its_total_duration_and_keeps_its_phase_order() -> None:
    load = DatasetLoad(
        mode=DatasetLoadMode.FULL,
        scanned=10,
        added=2,
        changed=3,
        unchanged=5,
        phases={"download": timedelta(seconds=30), "scan": timedelta(seconds=4)},
        cpu_time=timedelta(seconds=20),
    )

    restored = DatasetLoad.model_validate_json(load.model_dump_json())

    assert load.duration == timedelta(seconds=34)
    assert restored == load
    assert list(restored.phases) == ["download", "scan"]
    with pytest.raises(ValidationError):
        DatasetLoad(mode=DatasetLoadMode.FULL, scanned=-1)


def test_a_dataset_only_accompanies_a_completed_snapshot() -> None:
    failed = SourceSnapshot(
        id=uuid4(),
        source_id="nppes_monthly",
        fetched_at=NOW,
        outcome=Outcome.FAILED,
        source_url="https://example.test/files.html",
    )

    with pytest.raises(ValueError, match="completed snapshot"):
        DatasetFetch(snapshot=failed, mode=DatasetLoadMode.FULL, dataset=ListDataset())
    assert DatasetFetch(snapshot=failed, mode=DatasetLoadMode.FULL).dataset is None
