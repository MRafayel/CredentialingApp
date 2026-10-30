"""Ports: the interfaces outer layers implement and the application depends on."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from credentialing.domain.enums import CredentialType
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    FetchResult,
    Provider,
    SnapshotDiff,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.types import Npi, SourceId, StateCode


class Clock(Protocol):
    """The current time, injected so time-dependent logic is testable."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""
        ...


class ProviderRepository(Protocol):
    """Providers on organizations' rosters. Every call is scoped to one organization."""

    async def add(self, provider: Provider) -> None:
        """Store a new provider."""
        ...

    async def get(self, organization_id: UUID, provider_id: UUID) -> Provider | None:
        """Return the organization's provider with this id, if any."""
        ...

    async def find_by_npi(self, organization_id: UUID, npi: Npi) -> Provider | None:
        """Return the organization's provider with this NPI, if any."""
        ...

    async def list(
        self, organization_id: UUID, *, query: str | None, limit: int, offset: int
    ) -> tuple[Sequence[Provider], int]:
        """Return one page of the organization's providers, newest first, and the total.

        ``query`` matches last, first or organization name (case-insensitive substring) or
        the exact NPI; ``None`` or blank lists everyone.
        """
        ...


class SnapshotRepository(Protocol):
    """Fetches of public sources (shared reference data)."""

    async def add(self, snapshot: SourceSnapshot) -> None:
        """Store a new snapshot."""
        ...

    async def latest(self, source_id: SourceId) -> SourceSnapshot | None:
        """Return the source's most recent snapshot, whatever its outcome."""
        ...

    async def latest_completed(self, source_id: SourceId) -> SourceSnapshot | None:
        """Return the source's most recent ``COMPLETED`` snapshot: the list checks rely on."""
        ...


class ExclusionRecordRepository(Protocol):
    """Exclusion list rows, versioned by ``valid_from``/``valid_to`` (shared reference data).

    The *latest* row of an identity is its newest version, still listed (``valid_to`` null) or
    removed from the published list (``valid_to`` set). Reads return latest rows, so a party
    who left a list keeps matching and its removal can be reported, never read as clean.
    """

    async def replace_current(
        self,
        source_id: SourceId,
        snapshot_id: UUID,
        records: Sequence[ExclusionRecord],
        now: datetime,
        *,
        source_changed: bool,
    ) -> SnapshotDiff:
        """Make ``records`` the source's current list, atomically, and report the diff.

        For each incoming identity (``record_hash``):

        - never seen in the list: inserted as of ``now`` under ``snapshot_id`` (added);
        - removed earlier: a new version inserted as of ``now``, the removed one no longer
          latest (readded);
        - current with the same ``content_hash``: kept with its original ``valid_from``
          (unchanged);
        - current with a different ``content_hash``: the old version is closed with
          ``valid_to = now`` and no longer latest, the new one inserted as of ``now`` (changed).

        Current identities missing from ``records`` are closed with ``valid_to = now``. When
        ``source_changed`` (the snapshot's bytes differ from the previous completed one's)
        they stay latest (removed from the published list); otherwise only the parser changed,
        so they lose latest too (reparsed) and never read as removed. When ``source_changed``,
        a missing identity that pairs one to one by NPI and effective date with a never-seen
        incoming one (:func:`~credentialing.domain.policies.pair_rewritten_rows`) was rewritten
        by the list: it is closed and loses latest, and the new row's ``rewrites`` holds its id
        (rewritten, neither added nor removed). Rows are never deleted,
        so every version stays readable. Search keys
        derived from the names are refreshed on unchanged rows when the domain's name
        normalization has changed; that is derived data, not a new version.
        """
        ...

    async def find_candidates(
        self,
        npi: Npi | None,
        last_name: str | None,
        organization_name: str | None,
        *,
        license_numbers: Sequence[str] = (),
    ) -> Sequence[ExclusionRecord]:
        """Latest rows with this NPI (own or related), name, or licence number.

        The NPI is looked up as the row's own NPI and among its related NPIs. Names are the
        normalized last name, organization name, alias and related-party keys
        (:func:`~credentialing.domain.policies.candidate_keys`). Organization names are
        compared without legal designators, licence numbers without whitespace or case and
        in any state, so every record the matcher could weigh is found.
        """
        ...

    async def search(
        self,
        query: str,
        *,
        limit: int,
        source_ids: Sequence[SourceId] = (),
        state: StateCode | None = None,
    ) -> Sequence[ExclusionRecord]:
        """Latest rows matching a free-text lookup by name, NPI or licence, best first.

        Every word of ``query`` must start a word of the row's names (aliases and related
        parties included) or identifiers (related NPIs included); a ten-digit query also
        matches the NPI exactly, and those rows come first. With ``state``, only rows tied to
        that state: from one of ``source_ids`` (the state's own lists), or with a licence or
        address in it. At most ``limit`` rows, never more than 50.
        """
        ...

    async def get(self, record_id: UUID) -> ExclusionRecord | None:
        """Return the row with this id, current or not."""
        ...

    async def get_many(self, ids: Sequence[UUID]) -> Sequence[ExclusionRecord]:
        """Return the rows with these ids, current or not."""
        ...

    async def versions(self, source_id: SourceId, record_hash: str) -> Sequence[ExclusionRecord]:
        """Every version of one row identity in one source, current or not, oldest first."""
        ...

    async def rewritten_by(self, record_id: UUID) -> ExclusionRecord | None:
        """The row whose ``rewrites`` is ``record_id``: the version that replaced it when the
        list rewrote an identity field; None when it was not rewritten."""
        ...

    async def same_name(self, record: ExclusionRecord, *, limit: int) -> Sequence[ExclusionRecord]:
        """At most ``limit`` latest rows other than ``record`` whose name keys include its
        party key (:func:`~credentialing.domain.policies.party_key`), rows with the same first
        name first, then by source and id; none without a party key."""
        ...

    async def same_license(
        self, record: ExclusionRecord, *, limit: int
    ) -> Sequence[ExclusionRecord]:
        """At most ``limit`` latest rows other than ``record`` with its licence number
        (whitespace and case ignored) in the same state, by source and id; none without a
        licence."""
        ...

    async def linked_by_npi(
        self, npis: Sequence[Npi], *, exclude_id: UUID
    ) -> Sequence[ExclusionRecord]:
        """Latest rows other than ``exclude_id`` whose own or related NPIs include any of
        ``npis``, ordered by source and id; none when ``npis`` is empty."""
        ...

    async def count_current(self, source_id: SourceId) -> int:
        """Return how many of the source's rows are current (``valid_to`` is null)."""
        ...


class VerificationRepository(Protocol):
    """Verification history: append-only, scoped to one organization."""

    async def add(self, result: VerificationResult) -> None:
        """Append a verification result; earlier results are never changed."""
        ...

    async def latest(
        self,
        organization_id: UUID,
        provider_id: UUID,
        credential_type: CredentialType,
        source_id: SourceId,
    ) -> VerificationResult | None:
        """Return the most recent result for this provider, credential type and source."""
        ...

    async def latest_per_source(
        self, organization_id: UUID, provider_id: UUID, credential_type: CredentialType
    ) -> Sequence[VerificationResult]:
        """Return the most recent result of each source for this provider and credential type.

        One result per source, ordered by source id; results stored at the same moment are
        settled by id, so the answer is stable.
        """
        ...

    async def history(
        self, organization_id: UUID, provider_id: UUID, credential_type: CredentialType
    ) -> Sequence[VerificationResult]:
        """Return every result for this provider and credential type, newest first."""
        ...

    async def history_all(
        self, organization_id: UUID, provider_id: UUID
    ) -> Sequence[VerificationResult]:
        """Return every result for this provider across credential types, newest first."""
        ...


@runtime_checkable
class BulkListConnector(Protocol):
    """A source published as a whole list that is downloaded, parsed and diffed."""

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        ...

    async def fetch(self, previous: SourceSnapshot | None) -> FetchResult:
        """Fetch the list, using ``previous`` to skip the download when nothing changed."""
        ...


@runtime_checkable
class LookupConnector(Protocol):
    """A source queried one provider at a time."""

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        ...

    async def verify(self, provider: Provider) -> VerificationResult:
        """Check one provider and return a normalized result with provenance."""
        ...


class ConnectorDirectory(Protocol):
    """Where use cases find connectors: by source id or by capability, never by branching."""

    def get(self, source_id: SourceId) -> BulkListConnector | LookupConnector | None:
        """Return the connector for ``source_id``, if one is registered."""
        ...

    def bulk_connectors(self) -> Sequence[BulkListConnector]:
        """Every registered bulk-list connector, ordered by source id."""
        ...

    def bulk_connectors_for(
        self, credential_type: CredentialType, jurisdiction: StateCode
    ) -> Sequence[BulkListConnector]:
        """Bulk-list connectors covering this credential type in this jurisdiction."""
        ...
