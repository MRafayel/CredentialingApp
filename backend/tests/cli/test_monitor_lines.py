"""The ``monitor`` line and the sources report's monitoring line."""

import json
from datetime import timedelta
from uuid import uuid4

from credentialing.application.recheck_providers import NOTHING_DUE, MonitoringSummary
from credentialing.cli.main import monitor_summary
from credentialing.cli.sources import monitoring_line, render_json, render_table
from credentialing.domain.models import MonitoringRun
from tests.synthetic import NOW


def _run(**update: object) -> MonitoringRun:
    run = MonitoringRun(
        id=uuid4(),
        started_at=NOW - timedelta(hours=3, seconds=95.3),
        finished_at=NOW - timedelta(hours=3),
        trigger="due",
        organizations=3,
        providers=1_200,
        results=36_000,
        changed=4,
        failures=0,
        phases={
            "lists": timedelta(seconds=0.1),
            "providers": timedelta(seconds=1),
            "exclusions": timedelta(seconds=80),
            "npi": timedelta(seconds=14),
        },
        cpu_time=timedelta(seconds=60.1),
    )
    return run.model_copy(update=update)


def test_a_run_line_has_counts_times_and_phases() -> None:
    assert monitor_summary(_run()) == (
        "rechecked 1,200 providers in 3 organizations: results=36,000 changed=4 failures=0 "
        "duration=95.3s cpu=60.1s phases: lists 0s providers 1s exclusions 80s npi 14s"
    )


def test_a_list_run_names_its_list_and_a_run_that_checked_nothing_says_why() -> None:
    one = _run(source_id="ny_omig", organizations=1, phases={}, cpu_time=None)
    nothing = _run(providers=0, note=NOTHING_DUE)

    assert monitor_summary(one) == (
        "rechecked 1,200 providers against ny_omig in 1 organization: results=36,000 changed=4 "
        "failures=0 duration=95.3s"
    )
    assert monitor_summary(nothing) == f"rechecked 0 providers ({NOTHING_DUE})"


def test_a_run_that_purged_history_says_how_many_rows() -> None:
    purged = _run(purged=310, phases={"purge": timedelta(seconds=2)})
    nothing_due = _run(providers=0, note=NOTHING_DUE, purged=12, phases={})

    assert monitor_summary(purged) == (
        "rechecked 1,200 providers in 3 organizations: results=36,000 changed=4 failures=0 "
        "purged=310 duration=95.3s cpu=60.1s phases: purge 2s"
    )
    assert monitor_summary(nothing_due) == f"rechecked 0 providers ({NOTHING_DUE}, purged=12)"
    assert monitoring_line(MonitoringSummary(last_run=purged), NOW) == (
        "Monitoring: last run 3 h ago (due) · 1,200 providers · 4 changed · 0 failures · purged 310"
    )


def test_the_sources_report_ends_with_the_monitoring_line() -> None:
    lines = render_table([], NOW, color=False, monitoring=MonitoringSummary(last_run=_run()))

    assert lines.splitlines()[-1] == (
        "Monitoring: last run 3 h ago (due) · 1,200 providers · 4 changed · 0 failures"
    )
    assert monitoring_line(MonitoringSummary(last_run=None), NOW) == "Monitoring: never run"
    assert monitoring_line(MonitoringSummary(last_run=_run(failures=1)), NOW).endswith("1 failure")


def test_json_carries_the_last_run() -> None:
    payload = json.loads(render_json([], monitoring=MonitoringSummary(last_run=_run())))

    assert payload["monitoring"]["last_run"]["providers"] == 1_200
    assert payload["monitoring"]["last_run"]["phases"]["exclusions"] == "PT1M20S"
    assert json.loads(render_json([]))["monitoring"] is None
