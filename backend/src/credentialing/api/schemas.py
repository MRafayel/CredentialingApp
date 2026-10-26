"""HTTP request and response models, separate from domain models so the contract can evolve."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from credentialing.application.register_provider import ProviderInput
from credentialing.application.verification_history import VerificationView
from credentialing.domain.enums import (
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
from credentialing.domain.models import ExclusionRecord, Provider


class Schema(BaseModel):
    """Base for API models: unknown request fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class PersonNameBody(Schema):
    """A person's name in parts."""

    last: str = Field(min_length=1)
    first: str = Field(min_length=1)
    middle: str | None = None
    suffix: str | None = None


class PersonNameResponse(Schema):
    """A person's name in parts, as stored (a source may leave the first name empty)."""

    last: str
    first: str
    middle: str | None
    suffix: str | None


class LicenseBody(Schema):
    """A professional license."""

    number: str = Field(min_length=1)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    kind: str | None = None


class AddressBody(Schema):
    """A practice address; every part is optional."""

    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    postal_code: str | None = None


class ProviderCreate(Schema):
    """Request body of ``POST /providers``."""

    entity_kind: EntityKind
    person_name: PersonNameBody | None = None
    organization_name: str | None = None
    npi: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    date_of_birth: date | None = None
    licenses: list[LicenseBody] = Field(default_factory=list)
    addresses: list[AddressBody] = Field(default_factory=list)
    taxonomy_codes: list[str] = Field(default_factory=list)

    def to_input(self) -> ProviderInput:
        """The application input model for :class:`RegisterProvider`."""
        return ProviderInput.model_validate(self.model_dump())


class ProviderResponse(Schema):
    """A provider as the API returns it. The date of birth is never echoed back."""

    id: UUID
    entity_kind: EntityKind
    person_name: PersonNameResponse | None
    organization_name: str | None
    npi: str | None
    has_date_of_birth: bool
    licenses: list[LicenseBody]
    addresses: list[AddressBody]
    taxonomy_codes: list[str]
    created_at: datetime

    @classmethod
    def from_domain(cls, provider: Provider) -> ProviderResponse:
        """Map a domain provider into its HTTP shape."""
        name = provider.person_name
        return cls(
            id=provider.id,
            entity_kind=provider.entity_kind,
            person_name=PersonNameResponse.model_validate(name.model_dump()) if name else None,
            organization_name=provider.organization_name,
            npi=provider.npi,
            has_date_of_birth=provider.date_of_birth is not None,
            licenses=[LicenseBody.model_validate(item.model_dump()) for item in provider.licenses],
            addresses=[
                AddressBody.model_validate(item.model_dump()) for item in provider.addresses
            ],
            taxonomy_codes=list(provider.taxonomy_codes),
            created_at=provider.created_at,
        )


class MatchEvidenceResponse(Schema):
    """One field comparison behind a match decision."""

    field: MatchField
    matched: bool
    detail: str | None


class MatchResponse(Schema):
    """The match level, its evidence and a readable explanation."""

    level: MatchLevel
    evidence: list[MatchEvidenceResponse]
    explanation: str


class ProvenanceResponse(Schema):
    """Where and when the result came from."""

    source_id: str
    source_url: str
    method: VerificationMethod
    verified_at: datetime
    source_updated_at: datetime | None
    snapshot_id: UUID | None
    raw_record_ref: str | None


class MatchedRecordResponse(Schema):
    """An exclusion record a result points at. DOB and address are left out on purpose."""

    id: UUID
    source_id: str
    entity_kind: EntityKind
    person_name: PersonNameResponse | None
    organization_name: str | None
    aliases: list[str]
    npi: str | None
    action: ExclusionAction
    action_raw: str | None
    effective_date: date | None
    end_date: date | None

    @classmethod
    def from_domain(cls, record: ExclusionRecord) -> MatchedRecordResponse:
        """Map a domain record into its HTTP shape."""
        name = record.person_name
        return cls(
            id=record.id,
            source_id=record.source_id,
            entity_kind=record.entity_kind,
            person_name=PersonNameResponse.model_validate(name.model_dump()) if name else None,
            organization_name=record.organization_name,
            aliases=list(record.aliases),
            npi=record.npi,
            action=record.action,
            action_raw=record.action_raw,
            effective_date=record.effective_date,
            end_date=record.end_date,
        )


class VerificationResponse(Schema):
    """One verification result with provenance, freshness and the records it matched."""

    id: UUID
    provider_id: UUID
    credential_type: CredentialType
    source_id: str
    outcome: Outcome
    finding: Finding | None
    match: MatchResponse | None
    summary: str
    details: dict[str, str]
    provenance: ProvenanceResponse
    freshness: Freshness
    created_at: datetime
    matched_records: list[MatchedRecordResponse]

    @classmethod
    def from_view(cls, view: VerificationView) -> VerificationResponse:
        """Map a result and its expanded records into the HTTP shape."""
        result = view.result
        return cls(
            id=result.id,
            provider_id=result.provider_id,
            credential_type=result.credential_type,
            source_id=result.source_id,
            outcome=result.outcome,
            finding=result.finding,
            match=MatchResponse.model_validate(result.match.model_dump()) if result.match else None,
            summary=result.summary,
            details=dict(result.details),
            provenance=ProvenanceResponse.model_validate(result.provenance.model_dump()),
            freshness=result.freshness,
            created_at=result.created_at,
            matched_records=[MatchedRecordResponse.from_domain(r) for r in view.matched_records],
        )
