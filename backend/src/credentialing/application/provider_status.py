"""Use cases: a monitored provider's status, the roster's counts by attention, and "opened".

The status is derived by the domain (:func:`~credentialing.domain.policies.derive_status`)
from the provider's latest results, then stored, so a roster page can be ordered, filtered and
counted by attention in one query instead of reading every provider's history (at 10,000
providers that would be hundreds of thousands of rows per page). The stored row is derived
state: :class:`ProviderStatusKeeper` writes it after every check that appends results, after
the provider is opened, and for any provider whose row is missing or was derived by an older
version of the rules (:data:`~credentialing.domain.policies.STATUS_RULES_VERSION`) before a
roster read. Every call is scoped to one organization.

A status read while the provider's first check after an import is under way and none of its
results has landed is marked ``checking`` (:func:`with_checking`): the mark is never stored,
the reader asks the process running the checks (:class:`CheckTracking`) which providers are
still waiting. The roster's counts leave those providers out of their attention reason and
count them in ``checking`` instead, so "N need you" never counts a check that is running.
"""

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Final, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from credentialing.application.errors import ProviderNotFoundError
from credentialing.domain.enums import Attention, CredentialType
from credentialing.domain.models import Provider, ProviderStatus, VerificationResult
from credentialing.domain.policies import MONITORING_INTERVAL, STATUS_RULES_VERSION, derive_status
from credentialing.domain.ports import (
    Clock,
    ConnectorDirectory,
    ProviderRepository,
    VerificationRepository,
)

BATCH_SIZE: Final = 500
"""Providers whose results are read in one query when statuses are derived in bulk."""


class StatusRefreshing(Protocol):
    """What a check needs to keep the provider's stored status in step with its results."""

    async def refresh(self, provider: Provider) -> ProviderStatus:
        """Derive the provider's status from its latest results and store it."""
        ...


class CheckTracking(Protocol):
    """Who knows which providers' first check after an import is still under way (the
    process running it: :class:`~credentialing.application.first_checks.FirstChecks`)."""

    def waiting(self, organization_id: UUID) -> frozenset[UUID]:
        """The organization's providers whose scheduled first check has not finished."""
        ...


def with_checking(status: ProviderStatus, waiting: frozenset[UUID]) -> ProviderStatus:
    """The status marked ``checking`` when the provider is waiting for its first check and no
    result has landed yet; a result that has landed shows at once, whole run finished or not."""
    if status.provider_id in waiting and status.last_checked_at is None:
        return status.model_copy(update={"checking": True})
    return status


def waiting_in(tracking: CheckTracking | None, organization_id: UUID) -> frozenset[UUID]:
    """The providers waiting for their first check; none without a tracker (tools, tests).

    Always read *before* the statuses: a provider that leaves the set while the read is under
    way is then still marked checking (its row has no result yet) and read again, whereas a
    status read first and marked afterwards would settle as "not checked" for good.
    """
    return tracking.waiting(organization_id) if tracking is not None else frozenset()


class ProviderStatusKeeper:
    """Derives providers' statuses from their latest results and stores them.

    ``connectors`` names the connected exclusion lists, so a list that is no longer connected
    does not keep its providers due for a recheck (see
    :func:`~credentialing.domain.policies.derive_status`).
    """

    def __init__(
        self,
        providers: ProviderRepository,
        verifications: VerificationRepository,
        connectors: ConnectorDirectory,
        *,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._providers = providers
        self._verifications = verifications
        self._connectors = connectors
        self._batch_size = batch_size

    async def refresh(self, provider: Provider) -> ProviderStatus:
        """The provider's status, derived now and stored."""
        (status,) = await self.refresh_many(provider.organization_id, [provider])
        return status

    async def refresh_many(
        self, organization_id: UUID, providers: Sequence[Provider]
    ) -> list[ProviderStatus]:
        """Derive and store the statuses of these providers of one organization, a batch of
        :data:`BATCH_SIZE` per three reads and one write.

        Raises:
            ValueError: A provider belongs to another organization.
        """
        if any(provider.organization_id != organization_id for provider in providers):
            raise ValueError("every provider must belong to the organization")
        lists = self._lists()
        statuses: list[ProviderStatus] = []
        for start in range(0, len(providers), self._batch_size):
            batch = providers[start : start + self._batch_size]
            ids = [provider.id for provider in batch]
            exclusions = await self._verifications.latest_per_source_many(
                organization_id, ids, CredentialType.EXCLUSION
            )
            npis = await self._verifications.latest_per_source_many(
                organization_id, ids, CredentialType.NPI
            )
            changes = await self._verifications.last_changes(organization_id, ids)
            derived = [
                derive_status(
                    provider,
                    exclusions.get(provider.id, ()),
                    newest(npis.get(provider.id, ())),
                    lists=lists,
                    last_change_at=changes.get(provider.id),
                )
                for provider in batch
            ]
            await self._providers.save_statuses(
                organization_id, derived, rules_version=STATUS_RULES_VERSION
            )
            statuses.extend(derived)
        return statuses

    async def refresh_stale(self, organization_id: UUID) -> int:
        """Derive and store the statuses the organization lacks or holds from older rules;
        how many were derived."""
        stale = await self._providers.stale_statuses(organization_id, STATUS_RULES_VERSION)
        await self.refresh_many(organization_id, stale)
        return len(stale)

    def _lists(self) -> frozenset[str]:
        return frozenset(
            connector.capabilities.source_id
            for connector in self._connectors.bulk_connectors()
            if connector.capabilities.credential_type is CredentialType.EXCLUSION
        )


def newest(results: Sequence[VerificationResult]) -> VerificationResult | None:
    """The most recent of these results (ties settled by id), or None."""
    return max(results, key=lambda result: (result.created_at, str(result.id)), default=None)


class StatusSummary(BaseModel):
    """How many of the organization's providers have each attention reason (``None``: nothing
    needs a person), every reason present, the roster's total, and the recheck interval."""

    model_config = ConfigDict(frozen=True)

    counts: Mapping[Attention | None, int]
    """Per attention reason, without the providers whose first check is under way."""
    total: int
    """Every provider on the roster, those being checked included."""
    checking: int = 0
    """Providers whose first check after an import is under way with no result yet."""
    recheck_interval: timedelta = MONITORING_INTERVAL
    """How often every provider is rechecked (the monitoring policy)."""


class ProviderStatusQuery:
    """One provider's status, derived from its latest results now (and stored)."""

    def __init__(
        self,
        providers: ProviderRepository,
        keeper: ProviderStatusKeeper,
        *,
        checks: CheckTracking | None = None,
    ) -> None:
        self._providers = providers
        self._keeper = keeper
        self._checks = checks

    async def run(self, organization_id: UUID, provider_id: UUID) -> ProviderStatus:
        """The provider's status.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        waiting = waiting_in(self._checks, organization_id)
        provider = await self._providers.get(organization_id, provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        status = await self._keeper.refresh(provider)
        return with_checking(status, waiting)


class ProviderStatusSummary:
    """The roster's counts by attention reason, for the roster's filter chips."""

    def __init__(
        self,
        providers: ProviderRepository,
        keeper: ProviderStatusKeeper,
        *,
        checks: CheckTracking | None = None,
    ) -> None:
        self._providers = providers
        self._keeper = keeper
        self._checks = checks

    async def run(self, organization_id: UUID) -> StatusSummary:
        """Counts for every reason (zero included) and None, after deriving any missing or
        outdated status; providers whose first check is under way are counted in ``checking``
        and left out of their reason."""
        waiting = waiting_in(self._checks, organization_id)
        await self._keeper.refresh_stale(organization_id)
        stored = await self._providers.status_counts(organization_id)
        reasons: list[Attention | None] = [*Attention, None]
        counts = {reason: stored.get(reason, 0) for reason in reasons}
        total = sum(counts.values())
        checking = 0
        if waiting:
            under_way = await self._providers.unchecked_counts(organization_id, sorted(waiting))
            for reason, count in under_way.items():
                counts[reason] = max(counts[reason] - count, 0)
                checking += count
        return StatusSummary(counts=counts, total=total, checking=checking)


class MarkViewed:
    """Records that someone opened the provider, which clears "changed" for what they saw."""

    def __init__(
        self,
        providers: ProviderRepository,
        keeper: ProviderStatusKeeper,
        clock: Clock,
        *,
        checks: CheckTracking | None = None,
    ) -> None:
        self._providers = providers
        self._keeper = keeper
        self._clock = clock
        self._checks = checks

    async def run(self, organization_id: UUID, provider_id: UUID) -> ProviderStatus:
        """Set the provider's ``last_viewed_at`` to now and return its status as now derived.

        Raises:
            ProviderNotFoundError: The organization has no provider with this id.
        """
        waiting = waiting_in(self._checks, organization_id)
        provider = await self._providers.mark_viewed(
            organization_id, provider_id, self._clock.now()
        )
        if provider is None:
            raise ProviderNotFoundError(provider_id)
        status = await self._keeper.refresh(provider)
        return with_checking(status, waiting)
