"""The ``link-npis`` line: a linking run's counts, times and phases."""

from datetime import timedelta
from uuid import uuid4

from credentialing.cli.main import link_summary
from credentialing.domain.enums import MatchLevel
from credentialing.domain.models import NpiLinkRun
from tests.synthetic import NOW


def test_a_run_line_has_levels_times_and_phases() -> None:
    run = NpiLinkRun(
        id=uuid4(),
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=183.2),
        trigger="cli",
        mirror_snapshot_id=uuid4(),
        records=148_361,
        linked=98_480,
        by_level={
            MatchLevel.EXACT: 23_100,
            MatchLevel.HIGH: 4_210,
            MatchLevel.POTENTIAL: 19_870,
            MatchLevel.AMBIGUOUS: 51_300,
            MatchLevel.NONE: 49_881,
        },
        phases={
            "candidates": timedelta(seconds=60),
            "scoring": timedelta(seconds=95),
            "writing": timedelta(seconds=28),
        },
        cpu_time=timedelta(seconds=120.4),
    )

    assert link_summary(run) == (
        "linked 148,361 records: exact=23,100 high=4,210 potential=19,870 ambiguous=51,300 "
        "none=49,881 duration=183.2s cpu=120.4s phases: candidates 60s scoring 95s writing 28s"
    )


def test_a_run_that_could_not_link_says_why() -> None:
    run = NpiLinkRun(
        id=uuid4(),
        started_at=NOW,
        finished_at=NOW,
        trigger="cli",
        records=0,
        linked=0,
        note="the NPI mirror has never been loaded; nothing was linked",
    )

    assert link_summary(run) == (
        "linked 0 records (the NPI mirror has never been loaded; nothing was linked)"
    )
