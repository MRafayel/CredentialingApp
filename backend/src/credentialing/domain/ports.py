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
    """Exclusion list rows, versioned by ``valid_from``/``valid_to`` (shared reference data)."""

    async def replace_current(
        self,
        source_id: SourceId,
        snapshot_id: UUID,
        records: Sequence[ExclusionRecord],
        now: datetime,
    ) -> SnapshotDiff:
        """Make ``records`` the source's current list, atomically, and report the diff.

        For each incoming identity (``record_hash``):

        - not current: inserted as of ``now`` under ``snapshot_id`` (added);
        - current with the same ``content_hash``: kept with its original ``valid_from``
          (unchanged);
        - current with a different ``content_hash``: the old version is closed with
          ``valid_to = now`` and the new one inserted as of ``now`` (changed).

        Current identities missing from ``records`` are closed with ``valid_to = now``
        (removed). Rows are never deleted, so every version stays readable.
        """
        ...

    async def find_candidates(
        self, npi: Npi | None, last_name: str | None, organization_name: str | None
    ) -> Sequence[ExclusionRecord]:
        """Current rows with this NPI, or this normalized last name or organization name."""
        ...

    async def get_many(self, ids: Sequence[UUID]) -> Sequence[ExclusionRecord]:
        """Return the rows with these ids, current or not."""
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
