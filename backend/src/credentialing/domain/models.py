"""Domain models: immutable, validated, timezone-aware.

Every model is frozen. Results always carry provenance and an explicit freshness, and a check
that could not run is an outcome of its own, never a clean result.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Final, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    Finding,
    Freshness,
    MatchField,
    MatchLevel,
    Outcome,
    VerificationMethod,
)
from credentialing.domain.errors import InvalidProviderError
from credentialing.domain.names import normalize_name
from credentialing.domain.types import Npi, SourceId, StateCode


class DomainModel(BaseModel):
    """Base for every domain model: immutable and strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class PersonName(DomainModel):
    """A person's name in parts, as the source or customer gave it."""

    last: str
    first: str
    middle: str | None = None
    suffix: str | None = None


class Address(DomainModel):
    """A postal address; every part is optional because sources publish partial addresses."""

    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: StateCode | None = None
    postal_code: str | None = None


class License(DomainModel):
    """A professional license: its number and the issuing state."""

    number: str
    state: StateCode
    kind: str | None = None


class Provider(DomainModel):
    """A provider on an organization's roster: the subject of every verification."""

    id: UUID
    organization_id: UUID
    entity_kind: EntityKind
    person_name: PersonName | None = None
    organization_name: str | None = None
    npi: Npi | None = None
    date_of_birth: date | None = None
    licenses: tuple[License, ...] = ()
    addresses: tuple[Address, ...] = ()
    taxonomy_codes: tuple[str, ...] = ()
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _name_fits_entity_kind(self) -> Self:
        if self.entity_kind is EntityKind.INDIVIDUAL and self.person_name is None:
            raise InvalidProviderError("an individual provider needs a person name")
        if self.entity_kind is EntityKind.ORGANIZATION and not self.organization_name:
            raise InvalidProviderError("an organization provider needs an organization name")
        return self


class Provenance(DomainModel):
    """Where and when a result came from, so every answer can be traced to its source."""

    source_id: SourceId
    source_url: str
    method: VerificationMethod
    verified_at: AwareDatetime
    source_updated_at: AwareDatetime | None = None
    snapshot_id: UUID | None = None
    raw_record_ref: str | None = None


class MatchEvidence(DomainModel):
    """One field comparison that contributed to a match decision."""

    field: MatchField
    matched: bool
    detail: str | None = None


class MatchResult(DomainModel):
    """A match level with the evidence behind it and a readable explanation."""

    level: MatchLevel
    evidence: tuple[MatchEvidence, ...]
    explanation: str


_RECORD_HASH_VERSION: Final = 1
"""Version of both hash layouts; bump it when either payload changes."""


def compute_record_hash(
    *,
    source_id: str,
    entity_kind: EntityKind,
    person_name: PersonName | None,
    organization_name: str | None,
    npi: str | None,
    state_provider_id: str | None,
    license: License | None,
    effective_date: date | None,
    action: ExclusionAction,
) -> str:
    """Return a stable SHA-256 over the normalized identifying fields of an exclusion row.

    This is the row's identity: the same row hashes equal in two snapshots even when the
    source changes letter case, punctuation or credential suffixes, or adds unrelated columns,
    which is how snapshot diffs tell a kept row from a new or removed one. What may change
    under a fixed identity is covered by :func:`compute_content_hash`.
    """

    def text(value: str | None) -> str | None:
        stripped = value.strip().upper() if value else None
        return stripped or None

    def name(value: str | None) -> str | None:
        return (normalize_name(value) or None) if value else None

    identity = {
        "v": _RECORD_HASH_VERSION,
        "source_id": source_id,
        "entity_kind": entity_kind.value,
        "last": name(person_name.last) if person_name else None,
        "first": name(person_name.first) if person_name else None,
        "middle": name(person_name.middle) if person_name else None,
        "suffix": name(person_name.suffix) if person_name else None,
        "organization": name(organization_name),
        "npi": npi,
        "state_provider_id": text(state_provider_id),
        "license_number": text(license.number) if license else None,
        "license_state": license.state if license else None,
        "effective_date": effective_date.isoformat() if effective_date else None,
        "action": action.value,
    }
    return _digest(identity)


def compute_content_hash(
    *,
    end_date: date | None,
    reason: str | None,
    action_raw: str | None,
    aliases: tuple[str, ...],
    date_of_birth: date | None,
    address: Address | None,
    state_provider_id: str | None,
    license: License | None,
    raw: Mapping[str, str],
) -> str:
    """Return a SHA-256 over the content of an exclusion row that can change under its identity.

    A row whose identity (:func:`compute_record_hash`) is unchanged but whose content hash
    differs was changed by the source, for example a reinstatement date that appeared, and is
    stored as a new version. Text is only trimmed, aliases are order-independent, and the raw
    row is hashed with sorted keys.
    """

    def text(value: str | None) -> str | None:
        return (value.strip() or None) if value else None

    content = {
        "v": _RECORD_HASH_VERSION,
        "end_date": end_date.isoformat() if end_date else None,
        "reason": text(reason),
        "action_raw": text(action_raw),
        "aliases": sorted({alias.strip() for alias in aliases} - {""}),
        "date_of_birth": date_of_birth.isoformat() if date_of_birth else None,
        "address": address.model_dump(mode="json") if address else None,
        "state_provider_id": text(state_provider_id),
        "license_kind": text(license.kind) if license else None,
        "raw": dict(raw),
    }
    return _digest(content)


def _digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class ExclusionRecord(DomainModel):
    """One normalized row of an exclusion list, as it stood between ``valid_from`` and ``valid_to``.

    ``record_hash`` is the row's identity across snapshots and ``content_hash`` its changeable
    content. Build new records with :meth:`create`, which computes both.
    """

    id: UUID
    source_id: SourceId
    snapshot_id: UUID
    entity_kind: EntityKind
    person_name: PersonName | None = None
    organization_name: str | None = None
    aliases: tuple[str, ...] = ()
    npi: Npi | None = None
    state_provider_id: str | None = None
    license: License | None = None
    date_of_birth: date | None = None
    address: Address | None = None
    action: ExclusionAction
    action_raw: str | None = None
    effective_date: date | None = None
    end_date: date | None = None
    reason: str | None = None
    raw: Mapping[str, str]
    record_hash: str
    content_hash: str
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None

    @classmethod
    def create(
        cls,
        *,
        id: UUID,
        source_id: str,
        snapshot_id: UUID,
        entity_kind: EntityKind,
        action: ExclusionAction,
        raw: Mapping[str, str],
        valid_from: AwareDatetime,
        person_name: PersonName | None = None,
        organization_name: str | None = None,
        aliases: tuple[str, ...] = (),
        npi: str | None = None,
        state_provider_id: str | None = None,
        license: License | None = None,
        date_of_birth: date | None = None,
        address: Address | None = None,
        action_raw: str | None = None,
        effective_date: date | None = None,
        end_date: date | None = None,
        reason: str | None = None,
    ) -> Self:
        """Build a record and compute its hashes so connectors never do it by hand."""
        record_hash = compute_record_hash(
            source_id=source_id,
            entity_kind=entity_kind,
            person_name=person_name,
            organization_name=organization_name,
            npi=npi,
            state_provider_id=state_provider_id,
            license=license,
            effective_date=effective_date,
            action=action,
        )
        content_hash = compute_content_hash(
            end_date=end_date,
            reason=reason,
            action_raw=action_raw,
            aliases=aliases,
            date_of_birth=date_of_birth,
            address=address,
            state_provider_id=state_provider_id,
            license=license,
            raw=raw,
        )
        return cls(
            id=id,
            source_id=source_id,
            snapshot_id=snapshot_id,
            entity_kind=entity_kind,
            person_name=person_name,
            organization_name=organization_name,
            aliases=aliases,
            npi=npi,
            state_provider_id=state_provider_id,
            license=license,
            date_of_birth=date_of_birth,
            address=address,
            action=action,
            action_raw=action_raw,
            effective_date=effective_date,
            end_date=end_date,
            reason=reason,
            raw=raw,
            record_hash=record_hash,
            content_hash=content_hash,
            valid_from=valid_from,
        )


class SourceSnapshot(DomainModel):
    """One fetch of a source: when, what came back, and the validators for the next poll."""

    id: UUID
    source_id: SourceId
    fetched_at: AwareDatetime
    outcome: Outcome
    source_url: str
    content_hash: str | None = None
    etag: str | None = None
    last_modified: AwareDatetime | None = None
    byte_size: int | None = Field(default=None, ge=0)
    row_count: int | None = Field(default=None, ge=0)
    note: str | None = None


class SnapshotDiff(DomainModel):
    """How a new snapshot changed a source's current records."""

    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    changed: int = Field(ge=0)
    unchanged: int = Field(ge=0)


class VerificationResult(DomainModel):
    """The normalized answer to one check of one provider against one source.

    ``finding`` exists only when the source was actually consulted (``COMPLETED``); a check
    that could not run has an outcome but no finding, so it can never look clean.
    """

    id: UUID
    organization_id: UUID
    provider_id: UUID
    credential_type: CredentialType
    source_id: SourceId
    outcome: Outcome
    finding: Finding | None = None
    match: MatchResult | None = None
    matched_record_ids: tuple[UUID, ...] = ()
    summary: str
    details: Mapping[str, str] = Field(default_factory=dict)
    provenance: Provenance
    freshness: Freshness
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _finding_only_when_completed(self) -> Self:
        if self.outcome is Outcome.COMPLETED and self.finding is None:
            raise ValueError("a completed check needs a finding")
        if self.outcome is not Outcome.COMPLETED and self.finding is not None:
            raise ValueError(f"a check with outcome {self.outcome} cannot have a finding")
        return self


class ConnectorCapabilities(DomainModel):
    """What a connector covers and how it reaches its source; the registry selects by this."""

    source_id: SourceId
    credential_type: CredentialType
    jurisdictions: frozenset[StateCode]
    access_policy: AccessPolicy
    change_signal: ChangeSignal
    method: VerificationMethod
    poll_interval: timedelta
    source_url: str
    display_name: str


class FetchResult(DomainModel):
    """A bulk fetch: the snapshot always, the parsed records only when there is a new list."""

    snapshot: SourceSnapshot
    records: tuple[ExclusionRecord, ...] | None = None

    @model_validator(mode="after")
    def _records_only_when_completed(self) -> Self:
        if self.records is not None and self.snapshot.outcome is not Outcome.COMPLETED:
            raise ValueError("records can only accompany a completed snapshot")
        return self
