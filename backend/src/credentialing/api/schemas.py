"""HTTP request and response models, separate from domain models so the contract can evolve."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from credentialing.application.list_providers import ProviderPage
from credentialing.application.lookup_exclusions import LookupRecord, LookupResult, SearchedSource
from credentialing.application.npi_lookup import LiveCheck, MirrorSource, NpiSearchResult, NpiView
from credentialing.application.npi_suggestions import NpiSuggestion, NpiSuggestions
from credentialing.application.record_profile import (
    LinkedRecord,
    ProfileRecord,
    RecordNpiLink,
    RecordProfile,
    RecordSource,
    RecordVersion,
)
from credentialing.application.register_provider import ProviderInput
from credentialing.application.source_status import SourceHealth, SourceStatus
from credentialing.application.verification_history import VerificationView
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    DatasetLoadMode,
    EntityKind,
    ExclusionAction,
    Finding,
    Freshness,
    MatchField,
    MatchLevel,
    MatchRelation,
    NpiStatus,
    Outcome,
    RecordLink,
    RecordStatus,
    RegistryStatus,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    ExclusionRecord,
    NpiRecord,
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
                # A roster address never has a country (customers enter U.S. addresses).
                AddressBody.model_validate(item.model_dump(exclude={"country"}))
                for item in provider.addresses
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
    """The match level, its evidence, a readable explanation and the role the provider was
    matched as (the listed party or a related party on the row)."""

    level: MatchLevel
    evidence: list[MatchEvidenceResponse]
    explanation: str
    relation: MatchRelation


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
    """An exclusion record a result points at. DOB and address are left out on purpose.

    ``aliases`` are other names of the same party; ``related_parties`` name other parties on
    the same row and ``related_npis`` the row's other NPIs, whose owner the row does not say.
    """

    id: UUID
    source_id: str
    entity_kind: EntityKind
    person_name: PersonNameResponse | None
    organization_name: str | None
    aliases: list[str]
    related_parties: list[str]
    npi: str | None
    related_npis: list[str]
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
            related_parties=list(record.related_parties),
            npi=record.npi,
            related_npis=list(record.related_npis),
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
    """An exclusion record a lookup found. The date of birth and street address are left out.

    ``status`` says whether it still stands, has ended, or was removed from its published list
    (reinstatement not confirmed).
    """

    license: RecordLicenseResponse | None
    place: RecordPlaceResponse | None
    raw_provider_type: str | None
    status: RecordStatus

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
            status=found.status,
        )


class SearchedSourceResponse(Schema):
    """A list a lookup searched; ``fetched_at`` is null when the list was never loaded.

    ``jurisdictions`` are the states the list covers, sorted; "US" is the federal list.
    """

    source_id: str
    display_name: str
    jurisdictions: list[str]
    list_last_updated: datetime | None
    fetched_at: datetime | None

    @classmethod
    def from_source(cls, source: SearchedSource) -> SearchedSourceResponse:
        """Map a searched source into its HTTP shape."""
        return cls.model_validate(source.model_dump())


class LookupResponse(Schema):
    """Body of ``GET /exclusions/search``: the matching records and every list consulted."""

    query: str
    state: str | None
    """The state the search was narrowed to (upper case), or null for every state."""
    records: list[LookupRecordResponse]
    sources: list[SearchedSourceResponse]

    @classmethod
    def from_result(cls, result: LookupResult) -> LookupResponse:
        """Map a lookup result into its HTTP shape."""
        return cls(
            query=result.query,
            state=result.state,
            records=[LookupRecordResponse.from_lookup(found) for found in result.records],
            sources=[SearchedSourceResponse.from_source(source) for source in result.sources],
        )


class ProfileRecordResponse(LookupRecordResponse):
    """A record on a profile, with its list's name, identifiers and whether it still stands."""

    source_name: str
    state_provider_id: str | None
    reason: str | None

    @classmethod
    def from_profile(cls, found: ProfileRecord) -> ProfileRecordResponse:
        """Map a profile record into its HTTP shape."""
        return cls(
            **LookupRecordResponse.from_lookup(found).model_dump(),
            source_name=found.source_name,
            state_provider_id=found.record.state_provider_id,
            reason=found.record.reason,
        )


class LinkedRecordResponse(ProfileRecordResponse):
    """Another record tied to the profiled one; ``via_npi`` is the NPI that ties them."""

    link: RecordLink
    via_npi: str | None

    @classmethod
    def from_linked(cls, found: LinkedRecord) -> LinkedRecordResponse:
        """Map a linked record into its HTTP shape."""
        return cls(
            **ProfileRecordResponse.from_profile(found).model_dump(),
            link=found.link,
            via_npi=found.via_npi,
        )


class RecordSourceResponse(SearchedSourceResponse):
    """The list a record comes from; ``source_url`` is null for a list no longer registered.

    ``freshness`` is how current the stored copy is by the central policy, shown with
    ``fetched_at``; null when the list was never loaded or is no longer registered.
    """

    source_url: str | None
    freshness: Freshness | None

    @classmethod
    def from_record_source(cls, source: RecordSource) -> RecordSourceResponse:
        """Map a record's source into its HTTP shape."""
        return cls.model_validate(source.model_dump())


class RecordVersionResponse(Schema):
    """One version of a row in its list; ``valid_to`` is null while it is current.

    ``rewritten`` is true on the first version of an identity that replaced the previous one
    when the list rewrote an identity field; ``name`` (last name first for a person), the
    licence and the NPI say what changed.
    """

    valid_from: datetime
    valid_to: datetime | None
    action: ExclusionAction
    action_raw: str | None
    end_date: date | None
    reason: str | None
    rewritten: bool
    name: str
    license_number: str | None
    license_state: str | None
    npi: str | None

    @classmethod
    def from_version(cls, version: RecordVersion) -> RecordVersionResponse:
        """Map a record version into its HTTP shape."""
        return cls.model_validate(version.model_dump())


class NpiSummaryResponse(Schema):
    """A compact view of one mirrored NPI. Never a street or mailing address: the practice
    city and state only, and addresses otherwise only as match evidence ("same ZIP").

    ``primary_taxonomy`` is the code of the primary taxonomy (no description table is kept);
    ``in_file`` is false once the latest full file no longer lists the NPI.
    """

    npi: str
    entity_kind: EntityKind
    status: NpiStatus
    person_name: PersonNameResponse | None
    organization_name: str | None
    credential: str | None
    primary_taxonomy: str | None
    practice_city: str | None
    practice_state: str | None
    enumeration_date: date | None
    deactivation_date: date | None
    in_file: bool

    @classmethod
    def from_domain(cls, record: NpiRecord) -> NpiSummaryResponse:
        """Map a mirrored record into its compact HTTP shape."""
        return cls(**_npi_summary(record))


def _npi_summary(record: NpiRecord) -> dict[str, object]:
    name, place = record.person_name, record.practice_address
    primary = next((t.code for t in record.taxonomies if t.primary), None)
    return {
        "npi": record.npi,
        "entity_kind": record.entity_kind,
        "status": record.status,
        "person_name": PersonNameResponse.model_validate(name.model_dump()) if name else None,
        "organization_name": record.organization_name,
        "credential": record.credential,
        "primary_taxonomy": primary,
        "practice_city": place.city if place else None,
        "practice_state": place.state if place else None,
        "enumeration_date": record.enumeration_date,
        "deactivation_date": record.deactivation_date,
        "in_file": record.in_file,
    }


class NpiLinkResponse(Schema):
    """An inferred link from an exclusion record to an NPI of the mirror, for a person to weigh;
    never the record's published NPI. ``npi`` and ``registry`` are null on the row of a record
    checked and linked to nothing (level ``none``, the explanation says why). ``candidates``
    is how many mirror rows the search found (51: more than 50)."""

    npi: str | None
    level: MatchLevel
    evidence: list[MatchEvidenceResponse]
    explanation: str
    candidates: int
    linked_at: datetime
    registry: NpiSummaryResponse | None

    @classmethod
    def from_link(cls, found: RecordNpiLink) -> NpiLinkResponse:
        """Map a record's NPI link into its HTTP shape."""
        link = found.link
        return cls(
            npi=link.npi,
            level=link.level,
            evidence=[
                MatchEvidenceResponse.model_validate(e.model_dump()) for e in link.match.evidence
            ],
            explanation=link.match.explanation,
            candidates=link.candidates,
            linked_at=link.linked_at,
            registry=NpiSummaryResponse.from_domain(found.registry) if found.registry else None,
        )


class RecordProfileResponse(Schema):
    """Body of ``GET /exclusions/records/{id}``: one record and what the lists hold around it.

    ``versions`` run oldest first, across rewrites of the row's identity by the list.
    ``same_party`` lists records by NPI, then by licence; ``related`` the records tied through
    the row's other NPIs; ``same_name`` records that only share the name, for a person to
    weigh. ``rewritten_by`` is the id of the record that replaced this one when the list
    rewrote it, null otherwise. ``npi_links`` are the record's inferred links to NPIs of the
    registry mirror, best level first (empty when never linked). Dates of birth, street
    addresses and raw rows are never included.
    """

    record: ProfileRecordResponse
    source: RecordSourceResponse
    first_seen: datetime
    versions: list[RecordVersionResponse]
    same_party: list[LinkedRecordResponse]
    related: list[LinkedRecordResponse]
    same_name: list[LinkedRecordResponse]
    rewritten_by: UUID | None
    npi_links: list[NpiLinkResponse]

    @classmethod
    def from_profile(cls, profile: RecordProfile) -> RecordProfileResponse:
        """Map a record profile into its HTTP shape."""
        return cls(
            record=ProfileRecordResponse.from_profile(profile.record),
            source=RecordSourceResponse.from_record_source(profile.source),
            first_seen=profile.first_seen,
            versions=[RecordVersionResponse.from_version(v) for v in profile.versions],
            same_party=[LinkedRecordResponse.from_linked(r) for r in profile.same_party],
            related=[LinkedRecordResponse.from_linked(r) for r in profile.related],
            same_name=[LinkedRecordResponse.from_linked(r) for r in profile.same_name],
            rewritten_by=profile.rewritten_by,
            npi_links=[NpiLinkResponse.from_link(link) for link in profile.npi_links],
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

    ``listed_in``, ``flagged_in`` (named as a related party of a listed party), ``review_in``
    and ``unchecked`` name sources by id; ``results`` are ordered by source id. The UI
    shows these as given and derives no status of its own.
    """

    verdict: Verdict
    sources_checked: int
    listed_in: list[str]
    flagged_in: list[str]
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
            flagged_in=list(verdict.flagged_in),
            review_in=list(verdict.review_in),
            unchecked=list(verdict.unchecked),
            oldest_list_updated_at=verdict.oldest_list_updated_at,
            last_checked_at=verdict.last_checked_at,
            results=[VerificationResponse.from_view(view) for view in views],
        )


class DatasetLoadResponse(Schema):
    """What loading one dataset file did: counts, phase durations (ISO 8601, in the order they
    ran) and CPU time."""

    mode: DatasetLoadMode
    scanned: int
    added: int
    changed: int
    unchanged: int
    outdated: int
    missing: int
    deactivated: int
    duplicates: int
    phases: dict[str, timedelta]
    cpu_time: timedelta | None


class SnapshotResponse(Schema):
    """One fetch of a public source; ``load`` when the fetch loaded a dataset file."""

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
    load: DatasetLoadResponse | None

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
    last_load: DatasetLoadResponse | None
    """A dataset source's most recent load; null for a list and before any file loaded."""

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
            last_load=(
                DatasetLoadResponse.model_validate(status.last_load.model_dump())
                if status.last_load
                else None
            ),
        )


class MirrorResponse(Schema):
    """Which NPPES load the mirror is as of: ``provenance.verified_at`` is when the file was
    fetched, ``source_updated_at`` the date CMS gave it; ``freshness`` by the central policy.
    The mirror is a cached copy, never a live registry answer."""

    provenance: ProvenanceResponse
    freshness: Freshness

    @classmethod
    def from_mirror(cls, mirror: MirrorSource) -> MirrorResponse:
        """Map the mirror's source into its HTTP shape."""
        return cls.model_validate(mirror.model_dump())


class NpiTaxonomyResponse(Schema):
    """One taxonomy of an NPI, with the licence the provider reported for it."""

    code: str
    primary: bool
    license_number: str | None
    license_state: str | None
    group: str | None


class AuthorizedOfficialResponse(Schema):
    """The person an organization named as authorized to act for it."""

    name: PersonNameResponse
    title: str | None


class NpiRecordResponse(NpiSummaryResponse):
    """One mirrored NPI in full, without addresses (practice city and state only).

    ``updated_at`` is when the mirror last changed the row.
    """

    replacement_npi: str | None
    name_prefix: str | None
    other_names: list[str]
    sole_proprietor: bool | None
    organization_subpart: bool | None
    last_updated: date | None
    certification_date: date | None
    reactivation_date: date | None
    taxonomies: list[NpiTaxonomyResponse]
    authorized_official: AuthorizedOfficialResponse | None
    updated_at: datetime | None

    @classmethod
    def from_record(cls, record: NpiRecord) -> NpiRecordResponse:
        """Map a mirrored record into its full HTTP shape."""
        official = record.authorized_official
        return cls(
            **_npi_summary(record),
            replacement_npi=record.replacement_npi,
            name_prefix=record.name_prefix,
            other_names=list(record.other_names),
            sole_proprietor=record.sole_proprietor,
            organization_subpart=record.organization_subpart,
            last_updated=record.last_updated,
            certification_date=record.certification_date,
            reactivation_date=record.reactivation_date,
            taxonomies=[
                NpiTaxonomyResponse.model_validate(t.model_dump()) for t in record.taxonomies
            ],
            authorized_official=(
                AuthorizedOfficialResponse(
                    name=PersonNameResponse.model_validate(official.name.model_dump()),
                    title=official.title,
                )
                if official
                else None
            ),
            updated_at=record.updated_at,
        )


class LiveCheckResponse(Schema):
    """What the live registry (the NPPES API) answered, as its own block: when it was asked
    (``provenance.verified_at``), ``outcome`` (``completed`` when it answered), ``found``
    (null unless it answered) and the request's ``note``. A failed request is never "not
    found"."""

    provenance: ProvenanceResponse
    freshness: Freshness
    outcome: Outcome
    found: bool | None
    note: str | None

    @classmethod
    def from_check(cls, check: LiveCheck) -> LiveCheckResponse:
        """Map a live check into its HTTP shape."""
        return cls.model_validate(check.model_dump())


class NpiLookupResponse(Schema):
    """Body of ``GET /npi/{npi}``: the backend's ``status`` for the NPI, its record (the live
    answer when the registry returned it, else the mirror's row, null when neither has it),
    and two separate blocks: the mirror's load and the live check (null when not asked)."""

    npi: str
    status: RegistryStatus
    record: NpiRecordResponse | None
    mirror: MirrorResponse | None
    live: LiveCheckResponse | None

    @classmethod
    def from_view(cls, view: NpiView) -> NpiLookupResponse:
        """Map one NPI's view into its HTTP shape."""
        return cls(
            npi=view.npi,
            status=view.registry_status,
            record=NpiRecordResponse.from_record(view.record) if view.record else None,
            mirror=MirrorResponse.from_mirror(view.mirror) if view.mirror else None,
            live=LiveCheckResponse.from_check(view.live) if view.live else None,
        )


class NpiQueryResponse(Schema):
    """The structured query a live search sent (blank fields are null)."""

    last_name: str | None
    first_name: str | None
    organization_name: str | None
    entity_kind: EntityKind | None
    state: str | None
    city: str | None
    postal_code: str | None


class NpiSearchResponse(Schema):
    """Body of ``GET /npi/search``. A mirror search has ``query_text`` and the mirror's NPIs,
    active first; a live search has ``query`` and the registry's results in its order, and
    ``live`` says what the request answered (a failed one has no records). ``mirror`` is the
    load the mirror is as of (null when it was never loaded)."""

    query_text: str
    query: NpiQueryResponse | None
    records: list[NpiSummaryResponse]
    mirror: MirrorResponse | None
    live: LiveCheckResponse | None

    @classmethod
    def from_result(cls, result: NpiSearchResult) -> NpiSearchResponse:
        """Map a search into its HTTP shape."""
        query = result.query
        return cls(
            query_text=result.query_text,
            query=(
                NpiQueryResponse(
                    last_name=query.last_name,
                    first_name=query.first_name,
                    organization_name=query.organization_name,
                    entity_kind=query.entity_kind,
                    state=query.state,
                    city=query.city,
                    postal_code=query.postal_code,
                )
                if query
                else None
            ),
            records=[NpiSummaryResponse.from_domain(record) for record in result.records],
            mirror=MirrorResponse.from_mirror(result.mirror) if result.mirror else None,
            live=LiveCheckResponse.from_check(result.live) if result.live else None,
        )


class NpiSuggestionResponse(Schema):
    """One NPI the provider may have, for the user to weigh and confirm; never EXACT by name.
    ``npi`` and ``registry`` are null on the one row saying why nothing was suggested (level
    ``none``, the explanation says why). Never a street address."""

    npi: str | None
    level: MatchLevel
    evidence: list[MatchEvidenceResponse]
    explanation: str
    registry: NpiSummaryResponse | None

    @classmethod
    def from_suggestion(cls, suggestion: NpiSuggestion) -> NpiSuggestionResponse:
        """Map one suggestion into its HTTP shape."""
        match = suggestion.match
        return cls(
            npi=suggestion.npi,
            level=match.level,
            evidence=[MatchEvidenceResponse.model_validate(e.model_dump()) for e in match.evidence],
            explanation=match.explanation,
            registry=(
                NpiSummaryResponse.from_domain(suggestion.record) if suggestion.record else None
            ),
        )


class NpiSuggestionsResponse(Schema):
    """Body of ``GET /providers/{id}/npi-suggestions``: NPIs from the registry mirror for a
    provider without one, best first, computed now and never stored.

    ``mirror`` is the NPPES load the suggestions rest on (null when the mirror was never
    loaded, and then one ``none`` row says so); ``found`` how many mirror rows the search
    found (51: more than 50); ``note`` why nothing was searched (the provider has an NPI).
    """

    provider_id: UUID
    computed_at: datetime
    mirror: MirrorResponse | None
    found: int
    note: str | None
    suggestions: list[NpiSuggestionResponse]

    @classmethod
    def from_suggestions(cls, result: NpiSuggestions) -> NpiSuggestionsResponse:
        """Map the suggestions into their HTTP shape."""
        return cls(
            provider_id=result.provider_id,
            computed_at=result.computed_at,
            mirror=MirrorResponse.from_mirror(result.mirror) if result.mirror else None,
            found=result.found,
            note=result.note,
            suggestions=[NpiSuggestionResponse.from_suggestion(s) for s in result.suggestions],
        )


class ProviderNpiBody(Schema):
    """Request body of ``PUT /providers/{id}/npi``: the NPI the user confirms."""

    npi: str = Field(pattern=r"^[0-9]{10}$")


class ProviderNpiResponse(Schema):
    """Body of ``PUT /providers/{id}/npi``: the provider with its NPI, and the NPI
    verification appended as the record of what NPPES said and who set the NPI."""

    provider: ProviderResponse
    verification: VerificationResponse
