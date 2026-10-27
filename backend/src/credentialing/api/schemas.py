"""HTTP request and response models, separate from domain models so the contract can evolve."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from credentialing.application.list_providers import ProviderPage
from credentialing.application.lookup_exclusions import LookupRecord, LookupResult, SearchedSource
from credentialing.application.register_provider import ProviderInput
from credentialing.application.source_status import SourceHealth, SourceStatus
from credentialing.application.verification_history import VerificationView
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
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    ExclusionRecord,
    Provider,
    ProviderVerdict,
    SourceSnapshot,
)


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


class ProviderPageResponse(Schema):
    """Body of ``GET /providers``: one page, newest first, and the total across pages."""

    items: list[ProviderResponse]
    total: int
    page: int
    page_size: int

    @classmethod
    def from_page(cls, page: ProviderPage) -> ProviderPageResponse:
        """Map a page of domain providers into its HTTP shape."""
        return cls(
            items=[ProviderResponse.from_domain(provider) for provider in page.items],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
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


class RecordLicenseResponse(Schema):
    """The licence an exclusion record names, as the source published it."""

    number: str
    state: str
    kind: str | None


class RecordPlaceResponse(Schema):
    """Where an exclusion record places the provider: city and state only, never the street."""

    city: str | None
    state: str | None


class LookupRecordResponse(MatchedRecordResponse):
    """An exclusion record a lookup found. The date of birth and street address are left out."""

    license: RecordLicenseResponse | None
    place: RecordPlaceResponse | None
    raw_provider_type: str | None

    @classmethod
    def from_lookup(cls, found: LookupRecord) -> LookupRecordResponse:
        """Map a found record into its HTTP shape."""
        record = found.record
        license, address = record.license, record.address
        place = (
            RecordPlaceResponse(city=address.city, state=address.state)
            if address is not None and (address.city or address.state)
            else None
        )
        return cls(
            **MatchedRecordResponse.from_domain(record).model_dump(),
            license=RecordLicenseResponse.model_validate(license.model_dump()) if license else None,
            place=place,
            raw_provider_type=found.provider_type,
        )


class SearchedSourceResponse(Schema):
    """A list a lookup searched; ``fetched_at`` is null when the list was never loaded."""

    source_id: str
    display_name: str
    list_last_updated: datetime | None
    fetched_at: datetime | None

    @classmethod
    def from_source(cls, source: SearchedSource) -> SearchedSourceResponse:
        """Map a searched source into its HTTP shape."""
        return cls.model_validate(source.model_dump())


class LookupResponse(Schema):
    """Body of ``GET /exclusions/search``: the matching records and every list consulted."""

    query: str
    records: list[LookupRecordResponse]
    sources: list[SearchedSourceResponse]

    @classmethod
    def from_result(cls, result: LookupResult) -> LookupResponse:
        """Map a lookup result into its HTTP shape."""
        return cls(
            query=result.query,
            records=[LookupRecordResponse.from_lookup(found) for found in result.records],
            sources=[SearchedSourceResponse.from_source(source) for source in result.sources],
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


class VerdictResponse(Schema):
    """A provider's verdict as the backend decided it, with the latest result of each source.

    ``listed_in``, ``review_in`` and ``unchecked`` name sources by id; ``results`` are ordered
    by source id. The UI shows these as given and derives no status of its own.
    """

    verdict: Verdict
    sources_checked: int
    listed_in: list[str]
    review_in: list[str]
    unchecked: list[str]
    oldest_list_updated_at: datetime | None
    last_checked_at: datetime | None
    results: list[VerificationResponse]

    @classmethod
    def from_verdict(
        cls, verdict: ProviderVerdict, views: Sequence[VerificationView]
    ) -> VerdictResponse:
        """Map a verdict and its results, expanded with their records, into the HTTP shape."""
        return cls(
            verdict=verdict.verdict,
            sources_checked=verdict.sources_checked,
            listed_in=list(verdict.listed_in),
            review_in=list(verdict.review_in),
            unchecked=list(verdict.unchecked),
            oldest_list_updated_at=verdict.oldest_list_updated_at,
            last_checked_at=verdict.last_checked_at,
            results=[VerificationResponse.from_view(view) for view in views],
        )


class SnapshotResponse(Schema):
    """One fetch of a public source."""

    id: UUID
    source_id: str
    fetched_at: datetime
    outcome: Outcome
    source_url: str
    content_hash: str | None
    etag: str | None
    last_modified: datetime | None
    byte_size: int | None
    row_count: int | None
    note: str | None

    @classmethod
    def from_domain(cls, snapshot: SourceSnapshot) -> SnapshotResponse:
        """Map a domain snapshot into its HTTP shape."""
        return cls.model_validate(snapshot.model_dump())


class SourceStatusResponse(Schema):
    """One source's links, latest fetches and the state of its data; durations in ISO 8601."""

    source_id: str
    display_name: str
    credential_type: CredentialType
    jurisdictions: list[str]
    access_policy: AccessPolicy
    change_signal: ChangeSignal
    poll_interval: timedelta
    download_url: str | None
    source_url: str
    latest: SnapshotResponse | None
    latest_completed: SnapshotResponse | None
    current_records: int
    health: SourceHealth
    freshness: Freshness | None
    freshness_max_age: timedelta
    data_as_of: datetime | None
    next_due: datetime | None
    problem: str | None

    @classmethod
    def from_status(cls, status: SourceStatus) -> SourceStatusResponse:
        """Map a source status into its HTTP shape."""
        latest, completed = status.latest, status.latest_completed
        return cls(
            source_id=status.source_id,
            display_name=status.display_name,
            credential_type=status.credential_type,
            jurisdictions=list(status.jurisdictions),
            access_policy=status.access_policy,
            change_signal=status.change_signal,
            poll_interval=status.poll_interval,
            download_url=status.download_url,
            source_url=status.source_url,
            latest=SnapshotResponse.from_domain(latest) if latest else None,
            latest_completed=SnapshotResponse.from_domain(completed) if completed else None,
            current_records=status.current_records,
            health=status.health,
            freshness=status.freshness,
            freshness_max_age=status.freshness_max_age,
            data_as_of=status.data_as_of,
            next_due=status.next_due,
            problem=status.problem,
        )
