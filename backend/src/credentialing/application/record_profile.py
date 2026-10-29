"""Use case: everything the stored lists say about one exclusion record.

From one row it gathers what is known around it: every version of the row in its list (what
it said and since when), other current rows that are the same party (same NPI, or the same
licence number and state), rows tied to it through the other NPIs it publishes, and rows that
only share the name, which a person must weigh. Most records have little beyond themselves;
the profile holds as much as the lists do and no more.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from typing import Final
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from credentialing.application.errors import RecordNotFoundError
from credentialing.application.lookup_exclusions import LookupRecord, SearchedSource, provider_type
from credentialing.domain.enums import (
    CredentialType,
    ExclusionAction,
    Freshness,
    RecordLink,
    RecordStatus,
)
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord
from credentialing.domain.policies import (
    FreshnessPolicyTable,
    license_key,
    record_status,
    shares_party_name,
)
from credentialing.domain.ports import (
    Clock,
    ConnectorDirectory,
    ExclusionRecordRepository,
    SnapshotRepository,
)
from credentialing.domain.types import Npi

MAX_SAME_NAME: Final = 25
"""The most same-name records a profile lists."""

SAME_NAME_CANDIDATES: Final = 4 * MAX_SAME_NAME
"""The most rows sharing the party key that are fetched for the same-name section (100).

The database narrows by the key and puts rows with the same first name first;
:func:`~credentialing.domain.policies.shares_party_name` then keeps the namesakes. Four times
the shown maximum leaves room for rows that share only the surname, while a very common
surname ("Smith": about 800 current rows) never loads in full."""

MAX_SAME_LICENSE: Final = 25
"""The most records with the same licence number and state a profile lists."""


class RecordSource(SearchedSource):
    """The list a record comes from, with the age of the copy that holds it."""

    source_url: str | None
    """The page people read; None when the source is no longer registered."""
    freshness: Freshness | None
    """How current the copy is, by the central policy; None when it is unknown."""


class ProfileRecord(LookupRecord):
    """A record with the name of its list and whether it still stands."""

    source_name: str
    status: RecordStatus


class LinkedRecord(ProfileRecord):
    """Another record tied to the profiled one, and how."""

    link: RecordLink
    via_npi: Npi | None = None
    """The NPI that ties the two, for the NPI-based links."""


class RecordVersion(BaseModel):
    """One version of the row in its list: what it said, from when until when."""

    model_config = ConfigDict(frozen=True)

    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    action: ExclusionAction
    action_raw: str | None
    end_date: date | None
    reason: str | None


class RecordProfile(BaseModel):
    """What the stored lists say about one record and the records around it.

    ``same_party`` holds records by NPI, then by licence; ``related`` the records tied through
    the row's other NPIs; ``same_name`` the records that only share the name. No record
    appears in two of them, and none is another version of the profiled row.
    """

    model_config = ConfigDict(frozen=True)

    record: ProfileRecord
    source: RecordSource
    first_seen: AwareDatetime
    versions: tuple[RecordVersion, ...]
    same_party: tuple[LinkedRecord, ...]
    related: tuple[LinkedRecord, ...]
    same_name: tuple[LinkedRecord, ...]


class RecordProfileQuery:
    """Builds a :class:`RecordProfile` in a few indexed queries."""

    def __init__(
        self,
        records: ExclusionRecordRepository,
        snapshots: SnapshotRepository,
        connectors: ConnectorDirectory,
        freshness: FreshnessPolicyTable,
        clock: Clock,
    ) -> None:
        self._records = records
        self._snapshots = snapshots
        self._connectors = connectors
        self._freshness = freshness
        self._clock = clock

    async def run(self, record_id: UUID) -> RecordProfile:
        """The profile of the record with this id, current or not.

        Raises:
            RecordNotFoundError: No record has this id.
        """
        record = await self._records.get(record_id)
        if record is None:
            raise RecordNotFoundError(record_id)
        capabilities = {
            connector.capabilities.source_id: connector.capabilities
            for connector in self._connectors.bulk_connectors()
            if connector.capabilities.credential_type is CredentialType.EXCLUSION
        }
        versions = await self._records.versions(record.source_id, record.record_hash)
        own_npis = [record.npi] if record.npi else []
        by_npi = await self._records.linked_by_npi(
            [*own_npis, *record.related_npis], exclude_id=record.id
        )
        by_licence = await self._records.same_license(record, limit=MAX_SAME_LICENSE)
        by_name = await self._records.same_name(record, limit=SAME_NAME_CANDIDATES)
        completed = await self._snapshots.latest_completed(record.source_id)
        own = capabilities.get(record.source_id)
        now = self._clock.now()
        freshness = (
            self._freshness.policy_for(own.credential_type, own.source_id).evaluate(
                completed.fetched_at, now, own.method
            )
            if own and completed
            else None
        )

        links = _Links(record, capabilities, now.date())
        same_party = [
            *links.add(by_npi, _same_npi),
            *links.add(by_licence, _same_license),
        ]
        related = links.add(by_npi, _npi_link)
        same_name = links.add(by_name, _same_name, limit=MAX_SAME_NAME)
        return RecordProfile(
            record=links.profile(record),
            source=RecordSource(
                source_id=record.source_id,
                display_name=own.display_name if own else record.source_id,
                jurisdictions=tuple(sorted(own.jurisdictions)) if own else (),
                source_url=own.source_url if own else None,
                list_last_updated=completed.last_modified if completed else None,
                fetched_at=completed.fetched_at if completed else None,
                freshness=freshness,
            ),
            first_seen=min((v.valid_from for v in versions), default=record.valid_from),
            versions=tuple(
                RecordVersion(
                    valid_from=v.valid_from,
                    valid_to=v.valid_to,
                    action=v.action,
                    action_raw=v.action_raw,
                    end_date=v.end_date,
                    reason=v.reason,
                )
                for v in versions or [record]
            ),
            same_party=tuple(same_party),
            related=tuple(related),
            same_name=tuple(same_name),
        )


type _Link = tuple[RecordLink, Npi | None]
type _Classifier = Callable[[ExclusionRecord, ExclusionRecord], _Link | None]


class _Links:
    """Places each other record in at most one section, never a version of the profiled row."""

    def __init__(
        self,
        record: ExclusionRecord,
        capabilities: Mapping[str, ConnectorCapabilities],
        today: date,
    ) -> None:
        self._record = record
        self._capabilities = capabilities
        self._today = today
        self._seen = {record.id}

    def profile(self, record: ExclusionRecord) -> ProfileRecord:
        own = self._capabilities.get(record.source_id)
        return ProfileRecord(
            record=record,
            provider_type=provider_type(record, own),
            source_name=own.display_name if own else record.source_id,
            status=record_status([record], self._today),
        )

    def add(
        self,
        candidates: Iterable[ExclusionRecord],
        classify: _Classifier,
        *,
        limit: int | None = None,
    ) -> list[LinkedRecord]:
        linked: list[LinkedRecord] = []
        for other in candidates:
            if limit is not None and len(linked) >= limit:
                break
            if other.id in self._seen or (
                other.source_id == self._record.source_id
                and other.record_hash == self._record.record_hash
            ):
                continue
            link = classify(self._record, other)
            if link is None:
                continue
            self._seen.add(other.id)
            base = self.profile(other)
            linked.append(
                LinkedRecord(
                    record=base.record,
                    provider_type=base.provider_type,
                    source_name=base.source_name,
                    status=base.status,
                    link=link[0],
                    via_npi=link[1],
                )
            )
        return linked


def _same_npi(record: ExclusionRecord, other: ExclusionRecord) -> _Link | None:
    if record.npi and other.npi == record.npi:
        return RecordLink.BY_NPI, record.npi
    return None


def _same_license(record: ExclusionRecord, other: ExclusionRecord) -> _Link | None:
    own, theirs = record.license, other.license
    if (
        own
        and theirs
        and own.state == theirs.state
        and license_key(own.number) == license_key(theirs.number)
    ):
        return RecordLink.BY_LICENSE, None
    return None


def _npi_link(record: ExclusionRecord, other: ExclusionRecord) -> _Link | None:
    """``other``'s own NPI is one of the related NPIs here, or ``other`` names one of ours."""
    if other.npi and other.npi in record.related_npis:
        return RecordLink.LISTED_AS, other.npi
    ours: Sequence[Npi] = (*([record.npi] if record.npi else []), *record.related_npis)
    shared = next((npi for npi in ours if npi in other.related_npis), None)
    return (RecordLink.NAMED_ALONGSIDE, shared) if shared else None


def _same_name(record: ExclusionRecord, other: ExclusionRecord) -> _Link | None:
    return (RecordLink.SAME_NAME, None) if shares_party_name(record, other) else None
