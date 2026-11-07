"""Ports: the interfaces outer layers implement and the application depends on."""

from collections.abc import Iterator, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from credentialing.domain.enums import CredentialType, DatasetLoadMode, NpiStatus, Outcome
from credentialing.domain.models import (
    ConnectorCapabilities,
    DatasetLoad,
    ExclusionRecord,
    FetchResult,
    NpiRecord,
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

    async def latest_loaded(self, source_id: SourceId) -> SourceSnapshot | None:
        """Return the source's most recent snapshot whose dataset was loaded (``load`` set).

        Unchanged polls load nothing, so this is usually older than ``latest_completed``.
        """
        ...

    async def set_load(self, snapshot_id: UUID, load: DatasetLoad) -> None:
        """Record what loading the snapshot's dataset did; its ``row_count`` becomes
        ``load.scanned``."""
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


class Dataset(Protocol):
    """A downloaded dataset file, read in two passes so a load parses only the rows it needs.

    Both passes are synchronous and stream the file (the loader drives them from a worker
    thread); neither keeps the rows in memory.

    Raises (from either pass):
        DatasetFormatError: A row is malformed; the message names its line, never its content.
    """

    def scan(self) -> Iterator[tuple[Npi, str]]:
        """Every row's NPI and ``line_hash``, in file order, without parsing the fields."""
        ...

    def records(self, npis: AbstractSet[Npi] | None) -> Iterator[NpiRecord]:
        """The records of the rows whose NPI is in ``npis`` (``None``: every row), in file
        order. Only those rows are parsed; each record's ``line_hash`` equals the scan's."""
        ...

    def close(self) -> None:
        """Release the file, and delete it when the connector downloaded it for this load."""
        ...


@dataclass(frozen=True, slots=True)
class DatasetFetch:
    """A dataset fetch: the snapshot always, the opened file only when a new one arrived.

    A plain dataclass, not a model: it carries an open file. It lives here rather than in
    ``models`` because it refers to the :class:`Dataset` port.

    Raises:
        ValueError: A dataset accompanies a snapshot that did not complete.
    """

    snapshot: SourceSnapshot
    mode: DatasetLoadMode
    dataset: Dataset | None = None

    def __post_init__(self) -> None:
        if self.dataset is not None and self.snapshot.outcome is not Outcome.COMPLETED:
            raise ValueError("a dataset can only accompany a completed snapshot")


class NpiRegistryRepository(Protocol):
    """The local mirror of the NPPES NPI registry (shared reference data).

    One row per NPI, its newest version; replaced versions are kept in a history. Rows are
    never deleted: an NPI a full file no longer lists is flagged (``in_file`` false).
    """

    async def load(
        self,
        dataset: Dataset,
        *,
        mode: DatasetLoadMode,
        snapshot_id: UUID,
        now: datetime,
        min_rows: int | None = None,
    ) -> DatasetLoad:
        """Apply ``dataset`` to the mirror atomically and report what changed.

        Two passes: the scan's line hashes are compared with the mirror's, then only the rows
        that differ are read and written, under ``snapshot_id`` as of ``now``. A ``FULL`` file
        also flags the mirrored NPIs it no longer lists, an ``INCREMENTAL`` one never does,
        and a ``DEACTIVATIONS`` file sets only status and deactivation date. Phase timings of
        ``scan``, ``compare``, ``read`` and ``apply`` are returned; nothing is written when
        any step fails.

        Raises:
            DatasetShrankError: ``min_rows`` is given and the file has fewer distinct NPIs;
                nothing was written.
            DatasetFormatError: A row of the file is malformed; nothing was written.
        """
        ...

    async def get(self, npi: Npi) -> NpiRecord | None:
        """Return the mirrored record of this NPI, listed in the latest full file or not."""
        ...

    async def count(self, *, status: NpiStatus | None = None) -> int:
        """Return how many mirrored NPIs the latest file lists (``in_file``), optionally only
        those with ``status``."""
        ...


@runtime_checkable
class DatasetConnector(Protocol):
    """A source published as a large dataset file that is downloaded to disk and loaded in a
    mirror by difference.

    Its fetch is named ``fetch_dataset``, not ``fetch``: runtime protocol checks compare
    member names only, so a shared name would let a dataset connector pass as a
    :class:`BulkListConnector` (and reach the exclusion checks).
    """

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        ...

    async def fetch_dataset(self, previous: SourceSnapshot | None) -> DatasetFetch:
        """Fetch the file, using ``previous`` to skip the download when nothing changed."""
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

    def get(
        self, source_id: SourceId
    ) -> BulkListConnector | DatasetConnector | LookupConnector | None:
        """Return the connector for ``source_id``, if one is registered."""
        ...

    def bulk_connectors(self) -> Sequence[BulkListConnector]:
        """Every registered bulk-list connector, ordered by source id."""
        ...

    def dataset_connectors(self) -> Sequence[DatasetConnector]:
        """Every registered dataset connector, ordered by source id."""
        ...

    def polled_connectors(self) -> Sequence[BulkListConnector | DatasetConnector]:
        """Every connector a sync polls (bulk lists and datasets), ordered by source id."""
        ...

    def bulk_connectors_for(
        self, credential_type: CredentialType, jurisdiction: StateCode
    ) -> Sequence[BulkListConnector]:
        """Bulk-list connectors covering this credential type in this jurisdiction."""
        ...
