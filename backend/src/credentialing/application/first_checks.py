"""The first check of providers just imported, run after the import has answered.

An import of thousands of rows answers in about a second: the rows are stored, their statuses
derived ("not checked", due now), and the check of every list and the NPI mirror
(:meth:`~credentialing.application.recheck_providers.RecheckProviders.run_for`, one run per
import) is scheduled on the event loop of the process that took the request (owner,
2026-11-13, after feeling the 40 s wait of 5,000 rows checked before the answer). While it
runs, the roster readers ask which providers are still waiting (:meth:`FirstChecks.waiting`),
so a row reads "checking" instead of "not checked" until its results land.

Process state only: a restart drops the running check and the waiting set, and nothing is
lost. Results already appended stand, the statuses of the rest read "not checked" and are due
now (``next_check_at`` None), so the next due recheck (``make monitor-due``) checks them. A
run that fails is logged by error class and organization id only, never a name or an NPI. At
shutdown the running checks are cancelled, not waited for.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from credentialing.application.recheck_providers import IMPORT_TRIGGER, FirstChecking

logger = logging.getLogger(__name__)

TASK_NAME = "first-check"
"""The name of every scheduled run's task on the loop."""


class FirstCheckScheduling(Protocol):
    """What an import needs: the first check of the providers it stored, after it answered."""

    def schedule(self, organization_id: UUID, provider_ids: Sequence[UUID]) -> None:
        """Check these providers of the organization once, after the caller has answered."""
        ...


class FirstChecks:
    """Runs scheduled first checks on the event loop and knows which are still under way."""

    def __init__(self, recheck: FirstChecking) -> None:
        self._recheck = recheck
        self._waiting: dict[UUID, set[UUID]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, organization_id: UUID, provider_ids: Sequence[UUID]) -> None:
        """Schedule one run over these providers of the organization; nothing for none."""
        ids = tuple(dict.fromkeys(provider_ids))
        if not ids:
            return
        self._waiting.setdefault(organization_id, set()).update(ids)
        task = asyncio.create_task(self._run(organization_id, ids), name=TASK_NAME)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def waiting(self, organization_id: UUID) -> frozenset[UUID]:
        """The organization's providers whose scheduled check has not finished."""
        return frozenset(self._waiting.get(organization_id, ()))

    @property
    def running(self) -> int:
        """How many scheduled runs have not finished."""
        return len(self._tasks)

    async def wait(self) -> None:
        """Wait until every scheduled run has finished (tests and tools, never a request)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def close(self) -> None:
        """Cancel the running checks and wait for them to stop (shutdown)."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, organization_id: UUID, ids: tuple[UUID, ...]) -> None:
        try:
            await self._recheck.run_for(organization_id, ids, trigger=IMPORT_TRIGGER)
        except asyncio.CancelledError:
            logger.warning(
                "first check after import cancelled organization_id=%s providers=%d",
                organization_id,
                len(ids),
            )
            raise
        except Exception as error:
            logger.warning(
                "first check after import failed organization_id=%s providers=%d error=%s",
                organization_id,
                len(ids),
                type(error).__name__,
            )
        finally:
            left = self._waiting.get(organization_id)
            if left is not None:
                left.difference_update(ids)
                if not left:
                    del self._waiting[organization_id]
