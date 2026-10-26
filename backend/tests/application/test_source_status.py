from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from credentialing.application.source_status import (
    SourceHealth,
    SourceStatus,
    SourceStatusReport,
)
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import CredentialType, Freshness, Outcome
from credentialing.domain.models import SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS, FreshnessPolicy, FreshnessPolicyTable
from tests.fakes import FixedClock, InMemoryRecords, InMemorySnapshots, ScriptedConnector
from tests.fakes import capabilities as make_capabilities
from tests.synthetic import NOW, exclusion

pytestmark = pytest.mark.anyio

SOURCE = "fake_federal"
POLL = timedelta(days=7)
"""The poll interval of :func:`tests.fakes.capabilities`."""


def _snapshot(
    outcome: Outcome = Outcome.COMPLETED,
    *,
    at: datetime = NOW,
    last_modified: datetime | None = None,
    note: str | None = None,
    source_id: str = SOURCE,
) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=source_id,
        fetched_at=at,
        outcome=outcome,
        source_url="https://example.test/list",
        last_modified=last_modified,
        note=note,
    )


async def _report(
    *snapshots: SourceSnapshot,
    now: datetime,
    records: InMemoryRecords | None = None,
    registry: ConnectorRegistry | None = None,
    freshness: FreshnessPolicyTable = DEFAULT_FRESHNESS,
) -> tuple[SourceStatus, ...]:
    if registry is None:
        registry = ConnectorRegistry()
        registry.register(ScriptedConnector(make_capabilities(SOURCE)))
    store = InMemorySnapshots()
    store.stored.extend(snapshots)
    report = SourceStatusReport(
        registry, store, records or InMemoryRecords(), freshness, FixedClock(now)
    )
    return await report.run()


async def test_a_source_without_a_completed_fetch_was_never_fetched() -> None:
    (status,) = await _report(now=NOW)

    assert status.health is SourceHealth.NEVER_FETCHED
    assert (status.latest, status.latest_completed) == (None, None)
    assert (status.freshness, status.data_as_of, status.next_due, status.problem) == (
        None,
        None,
        None,
        None,
    )
    assert status.current_records == 0


async def test_failed_attempts_before_any_list_still_say_why() -> None:
    failed = _snapshot(Outcome.BLOCKED, note="HTTP 403")

    (status,) = await _report(failed, now=NOW)

    assert status.health is SourceHealth.NEVER_FETCHED
    assert status.latest == failed
    assert status.problem == "HTTP 403"


async def test_a_failed_latest_fetch_is_failing_with_its_note() -> None:
    loaded = _snapshot(at=NOW)
    failed = _snapshot(Outcome.SOURCE_UNAVAILABLE, at=NOW + timedelta(hours=2), note="timeout")

    (status,) = await _report(loaded, failed, now=NOW + timedelta(hours=3))

    assert status.health is SourceHealth.FAILING
    assert status.latest == failed
    assert status.latest_completed == loaded
    assert status.problem == "timeout"
    assert status.next_due == NOW + POLL


async def test_failing_wins_over_overdue() -> None:
    loaded = _snapshot(at=NOW)
    failed = _snapshot(Outcome.FAILED, at=NOW + POLL, note="parse error")

    (status,) = await _report(loaded, failed, now=NOW + 2 * POLL)

    assert status.health is SourceHealth.FAILING


async def test_a_source_past_its_poll_interval_is_overdue() -> None:
    (status,) = await _report(_snapshot(at=NOW), now=NOW + POLL + timedelta(seconds=1))

    assert status.health is SourceHealth.OVERDUE
    assert status.next_due == NOW + POLL
    assert status.problem is None


async def test_a_source_due_exactly_now_is_still_ok() -> None:
    (status,) = await _report(_snapshot(at=NOW), now=NOW + POLL)

    assert status.health is SourceHealth.OK


async def test_an_ok_source_reports_its_records_links_and_derived_times() -> None:
    modified = NOW - timedelta(days=10)
    loaded = _snapshot(at=NOW, last_modified=modified)
    records = InMemoryRecords()
    rows = [exclusion(source_id=SOURCE, snapshot_id=loaded.id, last=n) for n in ("Aa", "Bb")]
    await records.replace_current(SOURCE, loaded.id, rows, NOW)

    (status,) = await _report(loaded, now=NOW + timedelta(hours=3), records=records)

    assert status.health is SourceHealth.OK
    assert status.current_records == 2
    assert status.data_as_of == modified
    assert status.next_due == NOW + POLL
    assert status.freshness is Freshness.CACHED
    assert status.freshness_max_age == timedelta(days=7)
    assert status.source_url == f"https://example.test/{SOURCE}"
    assert status.download_url == f"https://example.test/{SOURCE}/list.csv"
    assert status.credential_type is CredentialType.EXCLUSION


async def test_data_age_falls_back_to_the_fetch_time_without_last_modified() -> None:
    (status,) = await _report(_snapshot(at=NOW), now=NOW + timedelta(hours=1))

    assert status.data_as_of == NOW


async def test_freshness_comes_from_the_central_policy_for_this_source() -> None:
    table = FreshnessPolicyTable(
        policies={
            **DEFAULT_FRESHNESS.policies,
            (CredentialType.EXCLUSION, SOURCE): FreshnessPolicy(max_age=timedelta(hours=1)),
        }
    )

    (status,) = await _report(_snapshot(at=NOW), now=NOW + timedelta(hours=2), freshness=table)

    assert status.freshness is Freshness.STALE
    assert status.freshness_max_age == timedelta(hours=1)
    assert status.health is SourceHealth.OK


async def test_a_fetch_inside_the_live_window_is_live() -> None:
    (status,) = await _report(_snapshot(at=NOW), now=NOW + timedelta(seconds=30))

    assert status.freshness is Freshness.LIVE


async def test_sources_are_ordered_by_display_name_with_sorted_jurisdictions() -> None:
    registry = ConnectorRegistry()
    registry.register(
        ScriptedConnector(make_capabilities("aaa_list", frozenset({"NY"}), display_name="Zeta"))
    )
    registry.register(
        ScriptedConnector(
            make_capabilities("zzz_list", frozenset({"NY", "NJ"}), display_name="alpha")
        )
    )

    statuses = await _report(now=NOW, registry=registry)

    assert [s.source_id for s in statuses] == ["zzz_list", "aaa_list"]
    assert statuses[0].jurisdictions == ("NJ", "NY")
