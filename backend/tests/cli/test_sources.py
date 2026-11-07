import io
import json
import re
from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.application.source_status import SourceHealth, SourceStatus
from credentialing.cli.sources import WIDTH, links_line, render_json, render_table, show
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    Freshness,
    MatchLevel,
    Outcome,
)
from credentialing.domain.models import NpiLinkRun, NpiLinkSummary, SourceSnapshot
from tests.synthetic import NOW

_ANSI = re.compile(r"\033\[[0-9;]*m")
LIST_UPDATED = NOW - timedelta(days=12)
"""The agency's own list date in the fixtures, distinct from every fetch time."""


def _snapshot(
    source_id: str, outcome: Outcome, hours_ago: int, note: str | None = None
) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id=source_id,
        fetched_at=NOW - timedelta(hours=hours_ago),
        outcome=outcome,
        source_url=f"https://example.test/{source_id}.csv",
        note=note,
    )


def _status(
    source_id: str,
    health: SourceHealth,
    *,
    display_name: str | None = None,
    records: int = 0,
    note: str | None = None,
) -> SourceStatus:
    completed = (
        None
        if health is SourceHealth.NEVER_FETCHED
        else _snapshot(source_id, Outcome.COMPLETED, 200 if health is SourceHealth.OVERDUE else 3)
    )
    latest = (
        _snapshot(source_id, Outcome.SOURCE_UNAVAILABLE, 1, note)
        if health is SourceHealth.FAILING
        else completed
    )
    poll = timedelta(days=7)
    return SourceStatus(
        source_id=source_id,
        display_name=display_name or f"Fake {source_id} list",
        credential_type=CredentialType.EXCLUSION,
        jurisdictions=("NY",),
        access_policy=AccessPolicy.HTTP,
        change_signal=ChangeSignal.CONTENT_HASH,
        poll_interval=poll,
        download_url=f"https://example.test/{source_id}.csv",
        source_url=f"https://example.test/{source_id}",
        latest=latest,
        latest_completed=completed,
        current_records=records,
        health=health,
        freshness=Freshness.CACHED if completed else None,
        freshness_max_age=timedelta(days=7),
        data_as_of=LIST_UPDATED if completed else None,
        next_due=completed.fetched_at + poll if completed else None,
        problem=note if health is SourceHealth.FAILING else None,
    )


STATUSES = (
    _status("fake_ok", SourceHealth.OK, records=84021),
    _status("fake_overdue", SourceHealth.OVERDUE, records=12),
    _status("fake_failing", SourceHealth.FAILING, records=7, note="timeout after 60 s"),
    _status("fake_never", SourceHealth.NEVER_FETCHED),
)


def _table(statuses: tuple[SourceStatus, ...] = STATUSES, *, color: bool = False) -> str:
    return render_table(statuses, NOW, color=color)


def test_the_table_has_one_row_per_source() -> None:
    lines = _table().splitlines()

    for status in STATUSES:
        assert sum(line.startswith(status.display_name) for line in lines) == 1
    header = next(line for line in lines if line.startswith("Source "))
    for column in (
        "Jur.",
        "Last fetch",
        "Result",
        "Records",
        "List last updated",
        "Fresh",
        "Next due",
    ):
        assert column in header


def test_rows_show_result_symbols_relative_times_and_counts() -> None:
    lines = _table().splitlines()
    ok = next(line for line in lines if line.startswith("Fake fake_ok"))
    overdue = next(line for line in lines if line.startswith("Fake fake_overdue"))
    never = next(line for line in lines if line.startswith("Fake fake_never"))

    assert "✓ ok" in ok
    assert "3 h ago" in ok
    assert "84,021" in ok
    assert "in 6 d" in ok
    assert LIST_UPDATED.date().isoformat() in ok
    assert "! overdue" in overdue
    assert "overdue 1 d" in overdue
    assert "\N{EN DASH} never" in never


def test_a_loaded_list_without_a_stated_date_says_not_stated() -> None:
    unstated = STATUSES[0].model_copy(update={"data_as_of": None})

    lines = _table((unstated, STATUSES[3])).splitlines()
    row = next(line for line in lines if line.startswith("Fake fake_ok"))
    never = next(line for line in lines if line.startswith("Fake fake_never"))

    assert "3 h ago" in row
    assert "not stated" in row
    assert (NOW - timedelta(hours=3)).date().isoformat() not in row
    assert "not stated" not in never


def test_a_failing_row_is_followed_by_its_outcome_and_note() -> None:
    lines = _table().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("Fake fake_failing"))

    assert "✕ failing" in lines[index]
    assert lines[index + 1] == "  ↳ source_unavailable: timeout after 60 s"
    assert sum("↳" in line for line in lines) == 1


def test_the_summary_counts_every_health_and_names_the_policy() -> None:
    lines = _table().splitlines()

    assert "4 sources · 1 ok · 1 overdue · 1 failing · 1 never fetched" in lines
    assert lines[-1] == "Data freshness policy: exclusions 7 d"


def test_long_names_are_truncated_to_keep_the_table_narrow() -> None:
    long = _status("fake_long", SourceHealth.OK, display_name="Very Long Source Name " * 8)

    table = _table((long,))

    assert all(len(line) <= WIDTH for line in table.splitlines())
    assert "…" in table


def test_colour_only_wraps_text_that_is_there_anyway() -> None:
    plain = _table()
    coloured = _table(color=True)

    assert "\033[" not in plain
    assert "\033[32m" in coloured
    assert "\033[31m" in coloured
    assert _ANSI.sub("", coloured) == plain


def test_an_empty_registry_renders_a_summary() -> None:
    assert "0 sources · 0 ok" in _table(())


def test_json_round_trips_with_every_field() -> None:
    payload = json.loads(render_json(STATUSES))["sources"]

    assert [item["source_id"] for item in payload] == [s.source_id for s in STATUSES]
    assert set(payload[0]) == set(SourceStatus.model_fields)
    first, due = payload[0], STATUSES[0].next_due
    assert due is not None
    assert first["health"] == "ok"
    assert first["poll_interval"] == "P7D"
    assert first["next_due"] == due.isoformat().replace("+00:00", "Z")
    assert first["download_url"] == "https://example.test/fake_ok.csv"
    assert payload[2]["problem"] == "timeout after 60 s"
    assert payload[3]["latest"] is None


@pytest.mark.parametrize(
    ("statuses", "strict", "expected"),
    [
        (STATUSES, False, 0),
        (STATUSES, True, 1),
        (STATUSES[:1], True, 0),
        (STATUSES[1:2], True, 1),
        (STATUSES[2:3], True, 1),
        (STATUSES[3:], True, 1),
        ((), True, 0),
    ],
)
def test_strict_exits_with_1_unless_every_source_is_ok(
    statuses: tuple[SourceStatus, ...], strict: bool, expected: int
) -> None:
    out = io.StringIO()

    code = show(statuses, NOW, out=out, as_json=False, strict=strict, color=False)

    assert code == expected
    assert out.getvalue()


def test_show_writes_json_when_asked() -> None:
    out = io.StringIO()

    show(STATUSES, NOW, out=out, as_json=True, strict=False, color=True)

    assert len(json.loads(out.getvalue())["sources"]) == len(STATUSES)
    assert "\033[" not in out.getvalue()


LINKS = NpiLinkSummary(
    records=148_361,
    by_best_level={
        MatchLevel.EXACT: 23_100,
        MatchLevel.HIGH: 4_210,
        MatchLevel.POTENTIAL: 19_870,
        MatchLevel.AMBIGUOUS: 51_300,
        MatchLevel.NONE: 49_871,
    },
    never_checked=10,
    last_run=NpiLinkRun(
        id=uuid4(),
        started_at=NOW - timedelta(hours=3, minutes=3),
        finished_at=NOW - timedelta(hours=3),
        trigger="cli",
        records=148_361,
        linked=98_480,
    ),
)


def test_the_table_ends_with_the_npi_links_line() -> None:
    lines = render_table(STATUSES, NOW, color=False, links=LINKS).splitlines()

    assert lines[-1] == (
        "NPI links: last run 3 h ago · 148,361 records · exact 23,100 · high 4,210 · "
        "potential 19,870 · ambiguous 51,300 · none 49,871 · never checked 10"
    )


def test_links_never_run_say_so() -> None:
    never = NpiLinkSummary(records=12, never_checked=12)

    assert links_line(never, NOW) == (
        "NPI links: never run · 12 records · exact 0 · high 0 · potential 0 · ambiguous 0 · "
        "none 0 · never checked 12"
    )


def test_json_carries_the_npi_links_summary() -> None:
    payload = json.loads(render_json(STATUSES, LINKS))

    assert payload["npi_links"]["records"] == 148_361
    assert payload["npi_links"]["by_best_level"]["ambiguous"] == 51_300
    assert payload["npi_links"]["last_run"]["trigger"] == "cli"
    assert json.loads(render_json(STATUSES))["npi_links"] is None
