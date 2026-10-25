"""Made-up test data shared by the test suites. Nothing here describes a real person."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from credentialing.domain.enums import EntityKind, ExclusionAction
from credentialing.domain.models import ExclusionRecord, License, PersonName, Provider

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def make_npi(first_nine: str) -> str:
    """Append the NPI check digit, computed independently of the code under test.

    Luhn over ``80840`` + the nine digits: double every second digit from the right.
    """
    total = 0
    for position, char in enumerate(reversed("80840" + first_nine)):
        digit = int(char)
        if position % 2 == 0:
            digit = digit * 2 - 9 if digit * 2 > 9 else digit * 2
        total += digit
    return first_nine + str((10 - total % 10) % 10)


def individual(
    *,
    last: str = "Testperson",
    first: str = "Avery",
    middle: str | None = None,
    npi: str | None = None,
    date_of_birth: date | None = None,
    licenses: tuple[License, ...] = (),
    organization_id: UUID | None = None,
) -> Provider:
    """A made-up individual provider."""
    return Provider(
        id=uuid4(),
        organization_id=organization_id or uuid4(),
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last=last, first=first, middle=middle),
        npi=npi,
        date_of_birth=date_of_birth,
        licenses=licenses,
        created_at=NOW,
    )


def organization(name: str = "Sampleville Care Group", *, npi: str | None = None) -> Provider:
    """A made-up organization provider."""
    return Provider(
        id=uuid4(),
        organization_id=uuid4(),
        entity_kind=EntityKind.ORGANIZATION,
        organization_name=name,
        npi=npi,
        created_at=NOW,
    )


def exclusion(
    *,
    source_id: str = "test_exclusions",
    snapshot_id: UUID | None = None,
    entity_kind: EntityKind = EntityKind.INDIVIDUAL,
    last: str | None = "Testperson",
    first: str = "Avery",
    middle: str | None = None,
    organization_name: str | None = None,
    aliases: tuple[str, ...] = (),
    npi: str | None = None,
    date_of_birth: date | None = None,
    license: License | None = None,
    effective_date: date | None = date(2024, 3, 1),
    end_date: date | None = None,
    action: ExclusionAction = ExclusionAction.EXCLUSION,
    raw: Mapping[str, str] | None = None,
    valid_from: datetime = NOW,
) -> ExclusionRecord:
    """A made-up exclusion list row, hashed the way connectors build them."""
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=source_id,
        snapshot_id=snapshot_id or uuid4(),
        entity_kind=entity_kind,
        person_name=PersonName(last=last, first=first, middle=middle) if last else None,
        organization_name=organization_name,
        aliases=aliases,
        npi=npi,
        date_of_birth=date_of_birth,
        license=license,
        effective_date=effective_date,
        end_date=end_date,
        action=action,
        raw=raw if raw is not None else {"LASTNAME": last or "", "FIRSTNAME": first},
        valid_from=valid_from,
    )
