"""Use case: one NPI or a search, from the local NPI mirror and, when needed, the live registry.

The mirror is read first. The live registry (the NPPES API) is asked when the caller asks for
it, when the mirror has no row for the NPI, or when the mirror's load is stale
(:func:`~credentialing.domain.policies.live_check_needed`): one request, stored as a
snapshot, and what it found written into the mirror when newer
(``application/npi_mirror.py``). Every answer carries two separate blocks: the mirror's (which
NPPES file it is as of, how fresh by the central policy, never presented as live) and the
live check's (when it was asked, what it answered, or why it could not be asked). The
backend's one-word conclusion (:class:`~credentialing.domain.enums.RegistryStatus`) is
decided here, so no screen derives one.
"""

import time
from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict

from credentialing.application.link_npis import latest_mirror_load
from credentialing.application.npi_mirror import MirrorWriter
from credentialing.domain.enums import (
    CredentialType,
    Freshness,
    Outcome,
    RegistryStatus,
    VerificationMethod,
)
from credentialing.domain.models import NpiAnswer, NpiRecord, Provenance
from credentialing.domain.policies import (
    FreshnessPolicyTable,
    live_check_needed,
    registry_status,
)
from credentialing.domain.ports import (
    Clock,
    ConnectorDirectory,
    NpiQuery,
    NpiRegistryConnector,
    NpiRegistryRepository,
    SnapshotRepository,
)
from credentialing.domain.types import Npi

MAX_RESULTS: Final = 50
"""The most NPIs one mirror search returns."""

LIVE_DEFAULT_RESULTS: Final = 50
"""What one live search asks for by default: each request should bring as much as it
reasonably can."""

LIVE_MAX_RESULTS: Final = 200
"""The most NPIs one live search asks for: the registry's own cap; we never page."""


class MirrorSource(BaseModel):
    """Which NPPES load the mirror is as of, and how fresh that is.

    ``provenance.verified_at`` is when that file was fetched, ``source_updated_at`` the date
    the file carries (its ``Last-Modified``), ``snapshot_id`` the load's snapshot.
    """

    model_config = ConfigDict(frozen=True)

    provenance: Provenance
    freshness: Freshness


class LiveCheck(BaseModel):
    """What the live registry answered about this NPI or search, as its own block.

    ``provenance`` is the request (``source_url`` without a search's query string,
    ``verified_at`` when it was sent, ``raw_record_ref`` its snapshot), ``freshness`` by the
    central policy, ``outcome`` whether the registry answered, ``found`` whether it returned
    an NPI (None unless it answered) and ``note`` the snapshot's note.
    """

    model_config = ConfigDict(frozen=True)

    provenance: Provenance
    freshness: Freshness
    outcome: Outcome
    found: bool | None
    note: str | None


class NpiView(BaseModel):
    """One NPI: its record, the mirror's block, the live check's block and the conclusion.

    ``record`` is the live answer when the registry returned the NPI (with the mirror's
    deactivation history carried forward), else the mirror's row, or None when neither has
    it. ``mirror_record`` is the mirror's row as it was read before any live answer.
    """

    model_config = ConfigDict(frozen=True)

    npi: Npi
    record: NpiRecord | None
    mirror_record: NpiRecord | None
    mirror: MirrorSource | None
    live: LiveCheck | None
    registry_status: RegistryStatus


class NpiSearchResult(BaseModel):
    """The NPIs a search found and where they came from.

    A mirror search (``live`` None) has ``query_text`` and the mirror's rows, best first. A
    live search has ``query`` and the registry's results in its own order; a failed one has
    no records and says why in ``live``, never mirror rows in their place.
    """

    model_config = ConfigDict(frozen=True)

    query_text: str
    query: NpiQuery | None
    records: tuple[NpiRecord, ...]
    mirror: MirrorSource | None
    live: LiveCheck | None


class NpiLookup:
    """Reads the mirror for the API, and the live registry when needed or asked."""

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
        self._writer = MirrorWriter(registry, snapshots, clock)

    async def get(self, npi: Npi, *, live: bool = False) -> NpiView:
        """``npi`` from the mirror, and from the live registry when ``live`` or when
        :func:`~credentialing.domain.policies.live_check_needed` says so.

        A mirror row counts for the conclusion only once a file load is known (a row the live
        registry wrote before any file was loaded has no mirror provenance). A live request
        that fails leaves the mirror's answer in place, with the failure in ``live``; "no
        result" writes nothing.
        """
        mirrored = await self._registry.get(npi)
        mirror = await self.mirror()
        record = mirrored
        check: LiveCheck | None = None
        connector = self._live_connector()
        if connector is not None and live_check_needed(
            requested=live,
            mirrored=mirrored is not None,
            freshness=mirror.freshness if mirror else None,
        ):
            started = time.perf_counter()
            answer = await connector.lookup_npi(npi)
            elapsed = timedelta(seconds=time.perf_counter() - started)
            await self._snapshots.add(answer.snapshot)
            check = self._check(connector, answer)
            if answer.found and answer.records:
                seen, _ = await self._writer.store(
                    answer.records[:1], snapshot_id=answer.snapshot.id, request_time=elapsed
                )
                record = seen[0]
        known = mirrored if mirror is not None else None
        return NpiView(
            npi=npi,
            record=record,
            mirror_record=mirrored,
            mirror=mirror,
            live=check,
            registry_status=registry_status(known, check.found if check else None),
        )

    async def search(
        self,
        text: str = "",
        *,
        limit: int,
        live: bool = False,
        query: NpiQuery | None = None,
    ) -> NpiSearchResult:
        """NPIs matching ``text`` in the mirror, or ``query`` in the live registry (``live``).

        Mirror: exact by key (an NPI, or a name), at most ``limit`` and never more than
        :data:`MAX_RESULTS`; fewer than two letters or digits search nothing. Live: one
        request for at most ``limit`` (up to :data:`LIVE_MAX_RESULTS`), every result newer
        than the mirror stored in one load, the results in the registry's order.

        Raises:
            ValueError: ``live`` without a ``query``.
        """
        mirror = await self.mirror()
        if live:
            if query is None:
                raise ValueError("a live search needs a structured query")
            return await self._search_live(query, limit=limit, mirror=mirror)
        term = text.strip()
        found: Sequence[NpiRecord] = []
        if sum(char.isalnum() for char in term) >= 2:
            found = await self._registry.search(term, limit=min(max(limit, 1), MAX_RESULTS))
        return NpiSearchResult(
            query_text=term, query=None, records=tuple(found), mirror=mirror, live=None
        )

    async def _search_live(
        self, query: NpiQuery, *, limit: int, mirror: MirrorSource | None
    ) -> NpiSearchResult:
        connector = self._live_connector()
        if connector is None:
            return NpiSearchResult(query_text="", query=query, records=(), mirror=mirror, live=None)
        started = time.perf_counter()
        answer = await connector.search_npis(query, limit=min(max(limit, 1), LIVE_MAX_RESULTS))
        elapsed = timedelta(seconds=time.perf_counter() - started)
        await self._snapshots.add(answer.snapshot)
        records: tuple[NpiRecord, ...] = ()
        if answer.records:
            records, _ = await self._writer.store(
                answer.records, snapshot_id=answer.snapshot.id, request_time=elapsed
            )
        return NpiSearchResult(
            query_text="",
            query=query,
            records=records,
            mirror=mirror,
            live=self._check(connector, answer),
        )

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

    def _live_connector(self) -> NpiRegistryConnector | None:
        connectors = self._connectors.npi_registry_connectors()
        return connectors[0] if connectors else None

    def _check(self, connector: NpiRegistryConnector, answer: NpiAnswer) -> LiveCheck:
        """The live block of one answer, with its own provenance and freshness."""
        capabilities = connector.capabilities
        snapshot = answer.snapshot
        policy = self._freshness.policy_for(CredentialType.NPI, capabilities.source_id)
        return LiveCheck(
            provenance=Provenance(
                source_id=snapshot.source_id,
                source_url=snapshot.source_url,
                method=capabilities.method,
                verified_at=snapshot.fetched_at,
                snapshot_id=snapshot.id,
                raw_record_ref=f"source_snapshots/{snapshot.id}",
            ),
            freshness=policy.evaluate(snapshot.fetched_at, self._clock.now(), capabilities.method),
            outcome=snapshot.outcome,
            found=answer.found,
            note=snapshot.note,
        )
