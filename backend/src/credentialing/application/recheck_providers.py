"""Use case: recheck the monitored rosters, a system job over every organization, measured.

Providers are rechecked against the exclusion lists and, when they have an NPI, the NPI
mirror (never the live registry), every :data:`~credentialing.domain.policies.MONITORING_INTERVAL`
(``due``), against one list right after a sync of that list changed rows
(``list_sync:<source_id>``), against the mirror after an NPPES load that changed rows
(``nppes_load``, NPI only), on request (``cli``), or once for providers just added to a
roster (``import`` from a bulk import, ``add`` for one provider: their first check, one
organization only), and against the lists once a user confirmed a provider's NPI
(``npi_set``: the published NPI is the one evidence that can make a match exact; the NPI
verification was just appended by the confirmation). Results are appended as every check
appends them, each with what changed since the previous one, and each provider's stored
status follows.

Tenant isolation: the job reads the organization ids once, then works one organization at a
time; every read and write names that organization, and no statement spans organizations.
A provider whose recheck raises is counted and logged by error class and organization id
only (never a name or an NPI), and the run goes on. Every run is stored with its counts,
phase timings and CPU time.

History retention: a ``due`` or ``cli`` run purges each organization's results older than
:func:`~credentialing.domain.policies.history_cutoff` once that organization's providers are
checked (never the first check of new providers or the rechecks after a sync, a load or an
NPI confirmation); a purge that raises is counted as a failure and logged the same way.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from credentialing.application.check_exclusions import CheckExclusions, ListState
from credentialing.application.measure import cpu_seconds, phase
from credentialing.application.provider_status import ProviderStatusKeeper
from credentialing.application.verify_npi import VerifyNpi
from credentialing.domain.models import MonitoringRun, Provider, VerificationResult
from credentialing.domain.policies import history_cutoff, recheck_due
from credentialing.domain.ports import (
    Clock,
    MonitoringRunRepository,
    ProviderRepository,
    VerificationRepository,
)
from credentialing.domain.types import SourceId

logger = logging.getLogger(__name__)

DUE_TRIGGER: Final = "due"
CLI_TRIGGER: Final = "cli"
IMPORT_TRIGGER: Final = "import"
ADD_TRIGGER: Final = "add"
NPI_SET_TRIGGER: Final = "npi_set"

PURGING_TRIGGERS: Final = frozenset({DUE_TRIGGER, CLI_TRIGGER})
"""The runs after which each organization's history older than the retention window goes."""

NO_PROVIDERS: Final = "no providers on any roster; nothing was checked"
NOTHING_DUE: Final = "no provider was due; nothing was checked"
NONE_GIVEN: Final = "none of the given providers is on the roster; nothing was checked"


class MonitoringSummary(BaseModel):
    """Where roster monitoring stands, for the sources report: the last run (None: never)."""

    model_config = ConfigDict(frozen=True)

    last_run: MonitoringRun | None


class ProviderRechecking(Protocol):
    """What a sync needs to recheck the rosters after it changed rows (a
    :class:`RecheckProviders`, or a fake)."""

    async def run(
        self,
        *,
        trigger: str,
        source_id: SourceId | None = None,
        due_only: bool = False,
        npi_only: bool = False,
    ) -> MonitoringRun:
        """Recheck every organization's providers; see :meth:`RecheckProviders.run`."""
        ...


class FirstChecking(Protocol):
    """What adding providers needs to run their first check (a :class:`RecheckProviders`, or
    a fake)."""

    async def run_for(
        self,
        organization_id: UUID,
        provider_ids: Sequence[UUID],
        *,
        trigger: str,
        npi: bool = True,
    ) -> MonitoringRun:
        """Check these providers of one organization now (the lists, and the NPI unless
        ``npi`` is false); see :meth:`RecheckProviders.run_for`."""
        ...


@dataclass(slots=True)
class _Tally:
    """What a run has done so far; ``seen`` counts every roster provider, checked or not."""

    organizations: int = 0
    providers: int = 0
    results: int = 0
    changed: int = 0
    failures: int = 0
    purged: int = 0
    seen: int = 0

    def add(self, results: Sequence[VerificationResult]) -> None:
        self.results += len(results)
        self.changed += sum(1 for result in results if result.changed_from is not None)


class RecheckProviders:
    """Rechecks the rosters of every organization, one organization at a time."""

    def __init__(
        self,
        providers: ProviderRepository,
        check_exclusions: CheckExclusions,
        verify_npi: VerifyNpi,
        keeper: ProviderStatusKeeper,
        runs: MonitoringRunRepository,
        clock: Clock,
        *,
        verifications: VerificationRepository,
    ) -> None:
        self._providers = providers
        self._verifications = verifications
        self._check = check_exclusions
        self._verify = verify_npi
        self._keeper = keeper
        self._runs = runs
        self._clock = clock

    async def run(
        self,
        *,
        trigger: str,
        source_id: SourceId | None = None,
        due_only: bool = False,
        npi_only: bool = False,
    ) -> MonitoringRun:
        """Recheck and store the run.

        With ``source_id``: that exclusion list only, for every provider (no due logic).
        With ``npi_only``: the mirror-only NPI verification of every provider with an NPI.
        Otherwise every list and the mirror-only NPI verification; with ``due_only`` only for
        providers whose recheck is due (:func:`~credentialing.domain.policies.recheck_due`).
        Phases: ``lists`` (the lists' snapshots, read once), ``providers`` (reading the rosters
        and deriving missing statuses), ``exclusions`` and ``npi`` (each including its writes
        and the status that follows), and ``purge`` for a ``due`` or ``cli`` run: each
        organization's history older than the retention window, after its providers.

        Raises:
            UnknownSourceError: ``source_id`` is not a connected exclusion list.
        """
        started, cpu_started = self._clock.now(), cpu_seconds()
        phases: dict[str, timedelta] = {}
        lists: tuple[ListState, ...] = ()
        if not npi_only:
            with phase(phases, "lists"):
                lists = await self._check.lists(source_id)
        tally = _Tally()
        with phase(phases, "providers"):
            organization_ids = await self._providers.organization_ids()
        for organization_id in organization_ids:
            await self._organization(
                organization_id,
                lists=lists,
                npi=source_id is None,
                due_only=due_only and source_id is None and not npi_only,
                purge=trigger in PURGING_TRIGGERS,
                phases=phases,
                tally=tally,
            )
        note = None
        if not tally.seen:
            note = NO_PROVIDERS
        elif not tally.providers:
            note = NOTHING_DUE
        return await self._finish(
            trigger,
            source_id=source_id,
            started=started,
            cpu_started=cpu_started,
            phases=phases,
            tally=tally,
            note=note,
        )

    async def run_for(
        self,
        organization_id: UUID,
        provider_ids: Sequence[UUID],
        *,
        trigger: str = IMPORT_TRIGGER,
        npi: bool = True,
    ) -> MonitoringRun:
        """Check these providers of one organization now (every list and, unless ``npi`` is
        false, the mirror-only NPI verification, as a full recheck does) and store the run:
        the first check of providers just added, or the lists' recheck after an NPI was
        confirmed (``npi=False``: its verification is already appended). Ids the
        organization does not have are skipped, so another organization's providers are
        never read or checked."""
        started, cpu_started = self._clock.now(), cpu_seconds()
        phases: dict[str, timedelta] = {}
        with phase(phases, "lists"):
            lists = await self._check.lists()
        tally = _Tally()
        wanted = set(provider_ids)
        with phase(phases, "providers"):
            # Derives a status for every new provider, so one is stored even without a list.
            await self._keeper.refresh_stale(organization_id)
            roster = await self._providers.list_all(organization_id)
        chosen = [provider for provider, _ in roster if provider.id in wanted]
        for provider in chosen:
            await self._check_one(
                organization_id, provider, lists=lists, npi=npi, phases=phases, tally=tally
            )
        tally.seen = tally.providers = len(chosen)
        tally.organizations = 1 if chosen else 0
        return await self._finish(
            trigger,
            source_id=None,
            started=started,
            cpu_started=cpu_started,
            phases=phases,
            tally=tally,
            note=None if chosen else NONE_GIVEN,
        )

    async def summary(self) -> MonitoringSummary:
        """The most recently finished run, for the sources report."""
        return MonitoringSummary(last_run=await self._runs.last_run())

    async def _finish(
        self,
        trigger: str,
        *,
        source_id: SourceId | None,
        started: datetime,
        cpu_started: float,
        phases: dict[str, timedelta],
        tally: _Tally,
        note: str | None,
    ) -> MonitoringRun:
        """Store and log the run ``tally`` describes."""
        run = MonitoringRun(
            id=uuid4(),
            started_at=started,
            finished_at=max(self._clock.now(), started),
            trigger=trigger,
            source_id=source_id,
            organizations=tally.organizations,
            providers=tally.providers,
            results=tally.results,
            changed=tally.changed,
            failures=tally.failures,
            purged=tally.purged,
            phases=phases,
            cpu_time=timedelta(seconds=cpu_seconds() - cpu_started),
            note=note,
        )
        await self._runs.add_run(run)
        logger.info(
            "monitoring run done trigger=%s organizations=%d providers=%d results=%d changed=%d "
            "failures=%d purged=%d seconds=%.1f",
            trigger,
            run.organizations,
            run.providers,
            run.results,
            run.changed,
            run.failures,
            run.purged,
            run.duration.total_seconds(),
        )
        return run

    async def _organization(
        self,
        organization_id: UUID,
        *,
        lists: tuple[ListState, ...],
        npi: bool,
        due_only: bool,
        purge: bool,
        phases: dict[str, timedelta],
        tally: _Tally,
    ) -> None:
        """Recheck one organization's providers, then, with ``purge``, delete its history older
        than the retention window; every call names ``organization_id``."""
        with phase(phases, "providers"):
            await self._keeper.refresh_stale(organization_id)
            roster = await self._providers.list_all(organization_id)
        tally.seen += len(roster)
        now: datetime = self._clock.now()
        checked = 0
        for provider, status in roster:
            if due_only and not recheck_due(status.next_check_at if status else None, now):
                continue
            checked += 1
            await self._check_one(
                organization_id, provider, lists=lists, npi=npi, phases=phases, tally=tally
            )
        tally.providers += checked
        if checked:
            tally.organizations += 1
        if purge:
            with phase(phases, "purge"):
                await self._purge(organization_id, now, tally)

    async def _purge(self, organization_id: UUID, now: datetime, tally: _Tally) -> None:
        """Purge one organization's old history; a failure is counted and logged by error
        class and organization id only."""
        try:
            tally.purged += await self._verifications.purge(
                organization_id, before=history_cutoff(now)
            )
        except Exception as error:
            tally.failures += 1
            logger.warning(
                "history purge failed organization_id=%s error=%s",
                organization_id,
                type(error).__name__,
            )

    async def _check_one(
        self,
        organization_id: UUID,
        provider: Provider,
        *,
        lists: tuple[ListState, ...],
        npi: bool,
        phases: dict[str, timedelta],
        tally: _Tally,
    ) -> None:
        """Check one provider against ``lists`` and, with ``npi`` and an NPI on file, the
        mirror; a failure is counted and logged by error class and organization id only."""
        try:
            if lists:
                with phase(phases, "exclusions"):
                    tally.add(await self._check.check(provider, lists))
            if npi and provider.npi is not None:
                with phase(phases, "npi"):
                    result = await self._verify.run(organization_id, provider.id, mirror_only=True)
                    tally.add([result])
        except Exception as error:
            tally.failures += 1
            logger.warning(
                "recheck failed organization_id=%s error=%s",
                organization_id,
                type(error).__name__,
            )
