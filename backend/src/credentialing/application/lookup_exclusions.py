"""Use case: look anyone up directly in the stored exclusion lists, without a roster entry.

The answer always says which lists were searched and how old each one is, so "no matches"
can be read for what it is: nothing in these lists, as of these dates. A list that was never
loaded is reported as such, never counted as searched.
"""

from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict

from credentialing.domain.enums import CredentialType
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord
from credentialing.domain.ports import (
    ConnectorDirectory,
    ExclusionRecordRepository,
    SnapshotRepository,
)
from credentialing.domain.types import SourceId, StateCode

MIN_QUERY_LENGTH: Final = 2
"""Shorter queries (after trimming) are not searched."""

MAX_RESULTS: Final = 50
"""The most records one lookup returns."""


class SearchedSource(BaseModel):
    """One list a lookup covers, with the age of the copy that was searched."""

    model_config = ConfigDict(frozen=True)

    source_id: SourceId
    display_name: str
    list_last_updated: AwareDatetime | None
    """The source's own last-modified time for the searched copy, when it publishes one."""
    fetched_at: AwareDatetime | None
    """When the searched copy was fetched; None when the list was never loaded."""


class LookupRecord(BaseModel):
    """A record a lookup found, with the provider type its source wrote down."""

    model_config = ConfigDict(frozen=True)

    record: ExclusionRecord
    provider_type: str | None


class LookupResult(BaseModel):
    """The records matching a query, best first, and every list that was consulted."""

    model_config = ConfigDict(frozen=True)

    query: str
    state: StateCode | None
    """The state the search was narrowed to, if any."""
    records: tuple[LookupRecord, ...]
    sources: tuple[SearchedSource, ...]


class LookupExclusions:
    """Searches the current rows of every exclusion list by name, NPI or licence number."""

    def __init__(
        self,
        records: ExclusionRecordRepository,
        snapshots: SnapshotRepository,
        connectors: ConnectorDirectory,
    ) -> None:
        self._records = records
        self._snapshots = snapshots
        self._connectors = connectors

    async def run(self, query: str, *, limit: int, state: StateCode | None = None) -> LookupResult:
        """Search for ``query`` (trimmed); fewer than two characters searches nothing.

        ``limit`` is clamped to 1..50. With ``state``, only records tied to it: on one of the
        state's own lists, or with a licence or address there (a federal record included).
        Every list is still consulted that way, so sources are every registered exclusion
        list, ordered by display name.
        """
        term = query.strip()
        if len(term) < MIN_QUERY_LENGTH:
            return LookupResult(query=term, state=state, records=(), sources=())
        capabilities = {
            connector.capabilities.source_id: connector.capabilities
            for connector in self._connectors.bulk_connectors()
            if connector.capabilities.credential_type is CredentialType.EXCLUSION
        }
        own_lists = (
            [c.source_id for c in capabilities.values() if state in c.jurisdictions]
            if state
            else []
        )
        found = await self._records.search(
            term, limit=min(max(limit, 1), MAX_RESULTS), source_ids=own_lists, state=state
        )
        sources = [await self._searched(c) for c in capabilities.values()]
        return LookupResult(
            query=term,
            state=state,
            records=tuple(
                LookupRecord(
                    record=r, provider_type=provider_type(r, capabilities.get(r.source_id))
                )
                for r in found
            ),
            sources=tuple(sorted(sources, key=lambda s: (s.display_name.casefold(), s.source_id))),
        )

    async def _searched(self, capabilities: ConnectorCapabilities) -> SearchedSource:
        completed = await self._snapshots.latest_completed(capabilities.source_id)
        return SearchedSource(
            source_id=capabilities.source_id,
            display_name=capabilities.display_name,
            list_last_updated=completed.last_modified if completed else None,
            fetched_at=completed.fetched_at if completed else None,
        )


def provider_type(
    record: ExclusionRecord, capabilities: ConnectorCapabilities | None
) -> str | None:
    """The first provider-type column the source declares that this row fills in."""
    fields = capabilities.provider_type_fields if capabilities else ()
    for field in fields:
        value = record.raw.get(field, "").strip()
        if value:
            return value
    return None
