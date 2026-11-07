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
    Verdict,
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
    country: str | None = None
    """The country code as the source publishes it (NPPES: ``US`` or a foreign code); None
    when the source gives none."""


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
    """When the agency last updated the list, as the source states it; None when it states
    none. Never the fetch time."""
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
    relation: MatchRelation = MatchRelation.PARTY
    """Which role on the row the provider was matched as: the confidence is ``level``, the
    role is ``relation``."""


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
    related_npis: tuple[str, ...] = (),
    related_parties: tuple[str, ...] = (),
) -> str:
    """Return a SHA-256 over the content of an exclusion row that can change under its identity.

    A row whose identity (:func:`compute_record_hash`) is unchanged but whose content hash
    differs was changed by the source, for example a reinstatement date that appeared, and is
    stored as a new version. Text is only trimmed, aliases are order-independent, and the raw
    row is hashed with sorted keys. Related NPIs and related parties are hashed (sorted) only
    when the row has any: their keys are left out when empty, so rows stored before the fields
    existed keep their hash and a sync does not version them again.
    """

    def text(value: str | None) -> str | None:
        return (value.strip() or None) if value else None

    content: dict[str, object] = {
        "v": _RECORD_HASH_VERSION,
        "end_date": end_date.isoformat() if end_date else None,
        "reason": text(reason),
        "action_raw": text(action_raw),
        "aliases": sorted({alias.strip() for alias in aliases} - {""}),
        "date_of_birth": date_of_birth.isoformat() if date_of_birth else None,
        "address": _address_content(address),
        "state_provider_id": text(state_provider_id),
        "license_kind": text(license.kind) if license else None,
        "raw": dict(raw),
    }
    if related_npis:
        content["related_npis"] = sorted(set(related_npis))
    parties = sorted({party.strip() for party in related_parties} - {""})
    if parties:
        content["related_parties"] = parties
    return _digest(content)


def _address_content(address: Address | None) -> dict[str, object] | None:
    """An address as hashed: ``country`` only when set, so rows stored before the field existed
    keep their content hash."""
    if address is None:
        return None
    return address.model_dump(mode="json", exclude={"country"} if address.country is None else None)


def _digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class ExclusionRecord(DomainModel):
    """One normalized row of an exclusion list, as it stood between ``valid_from`` and ``valid_to``.

    ``record_hash`` is the row's identity across snapshots and ``content_hash`` its changeable
    content. Build new records with :meth:`create`, which computes both.

    ``aliases`` are other names of the same party (dba, aka, maiden or former names).
    ``related_parties`` name other parties on the same row (an owner on a business's row, a
    business on a person's row), and ``related_npis`` are the other valid NPIs the row
    publishes besides ``npi``. The row does not say which NPI belongs to which name.

    ``rewrites`` is the id of the row this version replaced when the list rewrote one of its
    identity fields (same NPI and effective date, one to one within a sync). It is storage
    bookkeeping set by the repository, never hashed.
    """

    id: UUID
    source_id: SourceId
    snapshot_id: UUID
    entity_kind: EntityKind
    person_name: PersonName | None = None
    organization_name: str | None = None
    aliases: tuple[str, ...] = ()
    related_parties: tuple[str, ...] = ()
    npi: Npi | None = None
    related_npis: tuple[Npi, ...] = ()
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
    rewrites: UUID | None = None

    @model_validator(mode="after")
    def _related_npis_are_other_npis(self) -> Self:
        if self.npi is not None and self.npi in self.related_npis:
            raise ValueError("related_npis cannot repeat the record's own npi")
        if len(set(self.related_npis)) != len(self.related_npis):
            raise ValueError("related_npis cannot repeat an NPI")
        return self

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
        related_parties: tuple[str, ...] = (),
        npi: str | None = None,
        related_npis: tuple[str, ...] = (),
        state_provider_id: str | None = None,
        license: License | None = None,
        date_of_birth: date | None = None,
        address: Address | None = None,
        action_raw: str | None = None,
        effective_date: date | None = None,
        end_date: date | None = None,
        reason: str | None = None,
    ) -> Self:
        """Build a record and compute its hashes so connectors never do it by hand.

        ``related_npis`` drops the record's own ``npi`` and repeats, so a connector can pass
        every NPI after the first as the row publishes them.
        """
        related = tuple(dict.fromkeys(other for other in related_npis if other != npi))
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
            related_npis=related,
            related_parties=related_parties,
        )
        return cls(
            id=id,
            source_id=source_id,
            snapshot_id=snapshot_id,
            entity_kind=entity_kind,
            person_name=person_name,
            organization_name=organization_name,
            aliases=aliases,
            related_parties=related_parties,
            npi=npi,
            related_npis=related,
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


class DatasetLoad(DomainModel):
    """What loading one dataset file into its mirror did: counts, phase timings and CPU time.

    ``scanned`` counts the file's rows and ``duplicates`` the rows repeating an NPI already
    seen in the file (the last one wins). ``added`` rows were new to the mirror, ``changed``
    rows replaced a stored version (or came back after a full file had dropped them), and
    ``unchanged`` rows were already stored as they are. ``outdated`` rows were older than the
    mirror's version (an incremental file published before the full file already loaded, or a
    deactivation the mirror shows reactivated since) and were left alone. ``missing`` (full
    files only) counts mirrored rows the file no longer has, now flagged; ``deactivated``
    (deactivation files only) counts rows whose status or deactivation date this load set.

    ``phases`` are wall-clock durations in the order they ran (``download``, ``scan``,
    ``compare``, ``read``, ``apply``), ``cpu_time`` the process's user plus system time over
    the fetch and the load. The owner reads them to judge what each file costs.
    """

    mode: DatasetLoadMode
    scanned: int = Field(ge=0)
    added: int = Field(default=0, ge=0)
    changed: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    outdated: int = Field(default=0, ge=0)
    missing: int = Field(default=0, ge=0)
    deactivated: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    phases: Mapping[str, timedelta] = Field(default_factory=dict)
    cpu_time: timedelta | None = None

    @property
    def duration(self) -> timedelta:
        """The wall-clock time of every phase measured."""
        return sum(self.phases.values(), timedelta(0))


class SourceSnapshot(DomainModel):
    """One fetch of a source: when, what came back, and the validators for the next poll.

    ``load`` is set once a dataset file this fetch downloaded was loaded into its mirror
    (``SnapshotRepository.set_load``); a fetch that loaded nothing, and every bulk-list fetch,
    has none.
    """

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
    load: DatasetLoad | None = None


class SnapshotDiff(DomainModel):
    """How a new snapshot changed a source's current records.

    ``readded`` counts rows that came back after a removal; ``added`` counts only rows never
    seen in the list before. ``reparsed`` counts rows that disappeared while the source's
    bytes were unchanged: a parser change superseded them, the list did not remove them.
    ``rewritten`` counts rows whose identity the list rewrote (a vanished row paired one to one
    with a new row by NPI and effective date); they are in neither ``added`` nor ``removed``.
    """

    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    changed: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    readded: int = Field(default=0, ge=0)
    reparsed: int = Field(default=0, ge=0)
    rewritten: int = Field(default=0, ge=0)


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


class ProviderVerdict(DomainModel):
    """A provider's verdict rolled up from the latest result of each source, with its figures.

    Built by :func:`credentialing.domain.policies.derive_verdict`, so every screen shows the
    same answer. ``listed_in`` names the sources listing the provider, ``flagged_in`` those
    naming them as a related party of a listed party, ``review_in`` those needing a person and
    ``unchecked`` those whose latest check could not run.
    """

    verdict: Verdict
    sources_checked: int = Field(ge=0)
    listed_in: tuple[SourceId, ...] = ()
    flagged_in: tuple[SourceId, ...] = ()
    review_in: tuple[SourceId, ...] = ()
    unchecked: tuple[SourceId, ...] = ()
    oldest_list_updated_at: AwareDatetime | None = None
    """The oldest agency-stated list date among the sources checked; None when no checked
    source states one."""
    last_checked_at: AwareDatetime | None = None
    results: tuple[VerificationResult, ...] = ()


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
    download_url: str | None = None
    """The file a bulk-list connector downloads; ``source_url`` is the page people read."""
    provider_type_fields: tuple[str, ...] = ()
    """Raw columns naming the provider's type, most specific first; a lookup shows the first
    one that is filled in, as the source wrote it."""
    crawl_delay: timedelta = timedelta(0)
    """Pause before every request to the source, as its robots.txt ``Crawl-delay`` asks."""
    browser_fallback: bool = False
    """A request the site refuses to scripts (``BLOCKED``) is repeated in a headed real browser;
    the fetch is otherwise identical, and the snapshot note says the browser was used."""
    browser_warm_up: bool = False
    """In the browser fallback, open ``source_url`` (the landing page) first in the same
    session, then the requested URL; for firewalls that refuse a direct file request but
    accept it after the page that links it. The browser stays on the landing page until it
    has loaded and the firewall's clearance cookie is set (bounded), dwells a few seconds
    more, and opens the URL with the landing page as its Referer; nothing is clicked."""
    browser_webdriver_disabled: bool = False
    """In the browser fallback, launch Chrome without its automation marker
    (``navigator.webdriver`` false, the ``--disable-blink-features=AutomationControlled``
    argument). Used for sites whose bot manager loops its own non-interactive challenge for a
    browser that reports the marker (Alaska); everything else stays Chrome's own identity, and
    no challenge is ever clicked."""
    browser_challenge_timeout: timedelta | None = None
    """How long the browser waits for a challenge to clear by itself, when the fetcher's
    default (45 s) is too short for the site; ``None`` keeps the default."""
    last_modified_is_meaningful: bool = True
    """Whether the source's ``Last-Modified`` header dates its content. When ``False`` the
    header is a serving timestamp (the request time) and is not kept on the snapshot, so it
    is never shown as the source's update time nor sent back as ``If-Modified-Since``."""


class FetchResult(DomainModel):
    """A bulk fetch: the snapshot always, the parsed records only when there is a new list."""

    snapshot: SourceSnapshot
    records: tuple[ExclusionRecord, ...] | None = None

    @model_validator(mode="after")
    def _records_only_when_completed(self) -> Self:
        if self.records is not None and self.snapshot.outcome is not Outcome.COMPLETED:
            raise ValueError("records can only accompany a completed snapshot")
        return self


class NpiTaxonomy(DomainModel):
    """One of a provider's taxonomies (specialties) in the NPI registry, with the licence the
    provider reported for it."""

    code: str
    primary: bool
    license_number: str | None = None
    license_state: StateCode | None = None
    group: str | None = None


class AuthorizedOfficial(DomainModel):
    """The person an organization named as authorized to act for it in the NPI registry."""

    name: PersonName
    prefix: str | None = None
    title: str | None = None
    credential: str | None = None


class NpiRecord(DomainModel):
    """One NPI as the NPPES registry publishes it: a row of the mirror (shared reference data).

    ``other_names`` are the other names the registry publishes for the same party (another
    organization name, the parent organization's legal name, a person's other name as "Last,
    First Middle"), type codes dropped, without repeats, in file order. ``line_hash`` is the
    SHA-256 of the file row the record was read from: a load compares it to tell an unchanged
    row without parsing it. ``snapshot_id`` is the file that last wrote the row; ``in_file``
    turns false once a full file no longer lists the NPI (rows are never deleted).

    A deactivated NPI may carry nothing but its number, status and deactivation date (the
    registry blanks the rest), so its entity kind is then ``UNKNOWN``; an active one has a
    known entity kind. An active individual has a person name. An active organization may
    lack its legal name: the November 2026 file publishes 31 such rows, so the mirror keeps
    them as published rather than refuse the file. Names never cross kinds: no person name on
    an organization, no organization name on an individual.
    """

    npi: Npi
    entity_kind: EntityKind
    status: NpiStatus
    replacement_npi: Npi | None = None
    person_name: PersonName | None = None
    name_prefix: str | None = None
    credential: str | None = None
    organization_name: str | None = None
    other_names: tuple[str, ...] = ()
    sole_proprietor: bool | None = None
    organization_subpart: bool | None = None
    enumeration_date: date | None = None
    last_updated: date | None = None
    certification_date: date | None = None
    deactivation_date: date | None = None
    deactivation_reason: str | None = None
    reactivation_date: date | None = None
    mailing_address: Address | None = None
    practice_address: Address | None = None
    taxonomies: tuple[NpiTaxonomy, ...] = ()
    authorized_official: AuthorizedOfficial | None = None
    line_hash: str = Field(min_length=1)
    snapshot_id: UUID
    first_seen_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    in_file: bool = True

    @model_validator(mode="after")
    def _names_fit_entity_kind(self) -> Self:
        match self.entity_kind:
            case EntityKind.UNKNOWN:
                if self.status is not NpiStatus.DEACTIVATED:
                    raise ValueError("only a deactivated NPI may have an unknown entity kind")
            case EntityKind.INDIVIDUAL:
                if self.organization_name is not None:
                    raise ValueError("an individual's record cannot have an organization name")
                if self.status is NpiStatus.ACTIVE and self.person_name is None:
                    raise ValueError("an active individual's record needs a person name")
            case EntityKind.ORGANIZATION:
                if self.person_name is not None:
                    raise ValueError("an organization's record cannot have a person name")
        return self


class NpiLink(DomainModel):
    """An inferred tie from an exclusion record to an NPI of the registry mirror, with its match.

    Never the record's published NPI (``ExclusionRecord.npi``), which only the list sets: this
    is evidence for a person to weigh. ``npi`` is None on the one row of a record that was
    checked and linked to nothing; ``match`` then says why ("no candidate with this name").
    ``candidates`` is how many mirror rows the discovery found for the record (``CANDIDATE_CAP
    + 1`` meaning more than the cap); ``mirror_snapshot_id`` the NPPES load the link was made
    against, so a newer load makes it stale. The level is the match's, held once.
    """

    id: UUID
    record_id: UUID
    npi: Npi | None
    match: MatchResult
    candidates: int = Field(ge=0)
    mirror_snapshot_id: UUID
    linked_at: AwareDatetime

    @property
    def level(self) -> MatchLevel:
        """How strongly the record points at the NPI; ``NONE`` on a record's no-link row."""
        return self.match.level

    @model_validator(mode="after")
    def _no_npi_only_without_a_match(self) -> Self:
        if (self.npi is None) != (self.match.level is MatchLevel.NONE):
            raise ValueError("a link has an NPI exactly when its level is not none")
        return self


class NpiLinkRun(DomainModel):
    """One linking run: what triggered it, what it linked, and what it cost.

    ``records`` counts the exclusion records it (re)linked, ``linked`` those with at least one
    link above ``NONE``, and ``by_level`` the records by their best link's level. ``phases``
    are wall-clock durations summed over the batches (``candidates``, ``scoring``,
    ``writing``), ``cpu_time`` the process's user plus system time over the run. ``trigger``
    is ``list_sync:<source_id>``, ``nppes_load`` or ``cli``; ``note`` says why a run linked
    nothing when it could not (the mirror never loaded).
    """

    id: UUID
    started_at: AwareDatetime
    finished_at: AwareDatetime
    trigger: str = Field(min_length=1)
    mirror_snapshot_id: UUID | None = None
    records: int = Field(ge=0)
    linked: int = Field(ge=0)
    by_level: Mapping[MatchLevel, int] = Field(default_factory=dict)
    phases: Mapping[str, timedelta] = Field(default_factory=dict)
    cpu_time: timedelta | None = None
    note: str | None = None

    @property
    def duration(self) -> timedelta:
        """Wall-clock time from start to finish."""
        return self.finished_at - self.started_at


class NpiLinkSummary(DomainModel):
    """Where linking stands over the latest exclusion records, for the sources dashboard.

    ``by_best_level`` counts the checked records by their best link's level (``NONE``: checked,
    nothing linked); ``never_checked`` the latest records with no link row at all.
    """

    records: int = Field(ge=0)
    by_best_level: Mapping[MatchLevel, int] = Field(default_factory=dict)
    never_checked: int = Field(ge=0)
    last_run: NpiLinkRun | None = None
