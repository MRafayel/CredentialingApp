"""The first check after an import: scheduled on the loop, tracked while it runs, never a
request's wait; a failure or a cancellation only clears the waiting set."""

import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from credentialing.application.first_checks import FirstChecks
from credentialing.domain.models import MonitoringRun
from tests.fakes import FakeFirstChecker

pytestmark = pytest.mark.anyio

ORG_A, ORG_B = UUID(int=0xA), UUID(int=0xB)


class _SlowChecker(FakeFirstChecker):
    """A first check that waits at a gate (one per run, keyed by its first provider) until
    released, so the waiting set can be read meanwhile."""

    def __init__(self, *, fail: Exception | None = None) -> None:
        super().__init__(fail=fail)
        self.gates: dict[UUID, asyncio.Event] = {}

    def gate(self, provider_id: UUID) -> asyncio.Event:
        return self.gates.setdefault(provider_id, asyncio.Event())

    async def run_for(
        self,
        organization_id: UUID,
        provider_ids: Sequence[UUID],
        *,
        trigger: str,
        npi: bool = True,
    ) -> MonitoringRun:
        await self.gate(provider_ids[0]).wait()
        return await super().run_for(organization_id, provider_ids, trigger=trigger, npi=npi)


async def test_a_scheduled_check_runs_after_the_caller_and_is_tracked_meanwhile() -> None:
    checker = _SlowChecker()
    checks = FirstChecks(checker)
    one, two = uuid4(), uuid4()

    checks.schedule(ORG_A, [one, two, one])

    assert checker.calls == []  # nothing ran before the caller yielded
    assert checks.waiting(ORG_A) == {one, two}
    assert checks.waiting(ORG_B) == frozenset()
    assert checks.running == 1
    checker.gate(one).set()
    await checks.wait()
    assert checker.calls == [(ORG_A, (one, two), "import", True)]
    assert checks.waiting(ORG_A) == frozenset()
    assert checks.running == 0


async def test_nothing_is_scheduled_for_no_providers() -> None:
    checker = FakeFirstChecker()
    checks = FirstChecks(checker)

    checks.schedule(ORG_A, [])
    await checks.wait()

    assert (checker.calls, checks.running) == ([], 0)


async def test_a_failed_check_clears_the_waiting_set_and_is_logged_without_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    checker = FakeFirstChecker(fail=RuntimeError("synthetic outage"))
    checks = FirstChecks(checker)
    provider_id = uuid4()

    with caplog.at_level(logging.WARNING, logger="credentialing.application.first_checks"):
        checks.schedule(ORG_A, [provider_id])
        await checks.wait()

    assert checks.waiting(ORG_A) == frozenset()
    (record,) = caplog.records
    assert "error=RuntimeError" in record.getMessage()
    assert "synthetic outage" not in record.getMessage()


async def test_closing_cancels_a_running_check() -> None:
    checker = _SlowChecker()
    checks = FirstChecks(checker)
    checks.schedule(ORG_A, [uuid4()])
    await asyncio.sleep(0)  # the run starts and waits at its gate

    await checks.close()

    assert (checker.calls, checks.running, checks.waiting(ORG_A)) == ([], 0, frozenset())


async def test_two_imports_of_one_organization_wait_together_and_clear_apart() -> None:
    checker = _SlowChecker()
    checks, one, two = FirstChecks(checker), uuid4(), uuid4()
    checks.schedule(ORG_A, [one])
    checks.schedule(ORG_A, [two])

    assert checks.waiting(ORG_A) == {one, two}
    checker.gate(two).set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert checks.waiting(ORG_A) == {one}
    checker.gate(one).set()
    await checks.wait()
    assert checks.waiting(ORG_A) == frozenset()
    assert [call[1] for call in checker.calls] == [(two,), (one,)]
