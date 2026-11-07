"""Use case: read the local NPI registry mirror, one NPI or a search, with its provenance.

Every answer says which NPPES file the mirror is as of: the newest load across the dataset
sources, when it was fetched, the date CMS published the file, and how fresh that is by the
central policy. The mirror is a cached copy, never presented as a live registry answer.
"""

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import NpiNotFoundError
from credentialing.application.link_npis import latest_mirror_load
from credentialing.domain.enums import CredentialType, Freshness, VerificationMethod
from credentialing.domain.models import NpiRecord, Provenance
from credentialing.domain.policies import FreshnessPolicyTable
from credentialing.domain.ports import (
    Clock,
    ConnectorDirectory,
    NpiRegistryRepository,
    SnapshotRepository,
)
from credentialing.domain.types import Npi

MAX_RESULTS: Final = 50
"""The most NPIs one search returns."""


class MirrorSource(BaseModel):
    """Which NPPES load the mirror is as of, and how fresh that is.

    ``provenance.verified_at`` is when that file was fetched, ``source_updated_at`` the date
    the file carries (its ``Last-Modified``), ``snapshot_id`` the load's snapshot.
    """

    model_config = ConfigDict(frozen=True)

    provenance: Provenance
    freshness: Freshness


class NpiView(BaseModel):
    """One mirrored NPI and the load the mirror is as of."""

    model_config = ConfigDict(frozen=True)

    record: NpiRecord
    mirror: MirrorSource | None


class NpiSearchResult(BaseModel):
    """The mirrored NPIs a search found, best first, and the load the mirror is as of."""

    model_config = ConfigDict(frozen=True)

    query: str
    records: tuple[NpiRecord, ...]
    mirror: MirrorSource | None


class NpiLookup:
    """Reads the mirror for the API: by NPI and by exact name key."""

    def __init__(
        self,
        registry: NpiRegistryRepository,
        connectors: ConnectorDirectory,
        snapshots: SnapshotRepository,
        freshness: FreshnessPolicyTable,
        clock: Clock,
    ) -> None:
        self._registry = registry
        self._connectors = connectors
        self._snapshots = snapshots
        self._freshness = freshness
        self._clock = clock

    async def get(self, npi: Npi) -> NpiView:
        """The mirrored record of ``npi``, listed in the latest full file or not.

        Raises:
            NpiNotFoundError: The mirror has no record of this NPI.
        """
        record = await self._registry.get(npi)
        if record is None:
            raise NpiNotFoundError(npi)
        return NpiView(record=record, mirror=await self.mirror())

    async def search(self, query: str, *, limit: int) -> NpiSearchResult:
        """NPIs matching ``query`` exactly (an NPI, or a name by key), at most ``limit``.

        Fewer than two letters or digits search nothing. Prefix and fuzzy search are not in
        stage 2; see :meth:`~credentialing.domain.ports.NpiRegistryRepository.search`.
        """
        term = query.strip()
        found: Sequence[NpiRecord] = []
        if sum(char.isalnum() for char in term) >= 2:
            found = await self._registry.search(term, limit=min(max(limit, 1), MAX_RESULTS))
        return NpiSearchResult(query=term, records=tuple(found), mirror=await self.mirror())

    async def mirror(self) -> MirrorSource | None:
        """The newest load across the dataset sources; None when the mirror was never loaded."""
        loaded = await latest_mirror_load(self._connectors, self._snapshots)
        if loaded is None:
            return None
        connector = self._connectors.get(loaded.source_id)
        method = connector.capabilities.method if connector else VerificationMethod.BULK_DATASET
        policy = self._freshness.policy_for(CredentialType.NPI, loaded.source_id)
        return MirrorSource(
            provenance=Provenance(
                source_id=loaded.source_id,
                source_url=loaded.source_url,
                method=method,
                verified_at=loaded.fetched_at,
                source_updated_at=loaded.last_modified,
                snapshot_id=loaded.id,
            ),
            freshness=policy.evaluate(loaded.fetched_at, self._clock.now(), method),
        )
