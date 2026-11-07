"""Rendering of ``sources``: the source monitoring dashboard, as a table or as JSON.

A result is always a symbol and a word, so colour only ever repeats what the text says.
Colour is the caller's decision (a terminal, and ``NO_COLOR`` unset). Under the table, one
line says where linking exclusion records to NPIs stands.
"""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, TextIO

from credentialing.application.source_status import SourceHealth, SourceStatus
from credentialing.cli.relative_time import ago, span, until
from credentialing.domain.enums import (
    CredentialType,
    DatasetLoadMode,
    Freshness,
    MatchLevel,
    Outcome,
)
from credentialing.domain.models import DatasetLoad, NpiLinkSummary

WIDTH: Final = 100
"""The table never gets wider than this; the source name column gives way."""

_GAP: Final = "  "
_MIN_NAME: Final = 12
_EMPTY: Final = "\N{EN DASH}"
_NOT_STATED: Final = "not stated"

_GREEN: Final = "\033[32m"
_YELLOW: Final = "\033[33m"
_RED: Final = "\033[31m"
_DIM: Final = "\033[2m"
_RESET: Final = "\033[0m"

_HEALTH: Final = {
    SourceHealth.OK: ("✓ ok", _GREEN),
    SourceHealth.OVERDUE: ("! overdue", _YELLOW),
    SourceHealth.FAILING: ("✕ failing", _RED),
    SourceHealth.NEVER_FETCHED: (f"{_EMPTY} never", _DIM),
}

_CREDENTIAL_LABELS: Final = {
    CredentialType.EXCLUSION: "exclusions",
    CredentialType.NPI: "NPI",
    CredentialType.STATE_LICENSE: "state licenses",
    CredentialType.BOARD_CERTIFICATION: "board certifications",
}

type _Cell = tuple[str, str | None]
"""Plain text and the colour to wrap it in, if any."""


def show(
    statuses: Sequence[SourceStatus],
    now: datetime,
    *,
    out: TextIO,
    as_json: bool,
    strict: bool,
    color: bool,
    links: NpiLinkSummary | None = None,
) -> int:
    """Write the dashboard (or its JSON) to ``out`` and return the process exit code."""
    out.write(
        render_json(statuses, links)
        if as_json
        else render_table(statuses, now, color=color, links=links)
    )
    return exit_code(statuses, strict=strict)


def exit_code(statuses: Sequence[SourceStatus], *, strict: bool) -> int:
    """0, or with ``strict`` 1 when any source is not ``OK``."""
    return 1 if strict and any(s.health is not SourceHealth.OK for s in statuses) else 0


def render_json(statuses: Sequence[SourceStatus], links: NpiLinkSummary | None = None) -> str:
    """``{"sources": [...], "npi_links": {...}}``: every status with all its fields, and the
    NPI linking summary (null when not given); timestamps and durations in ISO 8601."""
    payload = {
        "sources": [status.model_dump(mode="json") for status in statuses],
        "npi_links": links.model_dump(mode="json") if links else None,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_table(
    statuses: Sequence[SourceStatus],
    now: datetime,
    *,
    color: bool,
    width: int = WIDTH,
    links: NpiLinkSummary | None = None,
) -> str:
    """One row per source, a note line under each failed fetch and under each dataset source's
    last load, a summary, the freshness policy, and the NPI links line when given."""
    headers = (
        "Source",
        "Jur.",
        "Last fetch",
        "Result",
        "Records",
        "List last updated",
        "Fresh",
        "Next due",
    )
    rows = [_row(status, now) for status in statuses]
    widths = [max([len(h), *(len(row[i][0]) for row in rows)]) for i, h in enumerate(headers)]
    budget = width - sum(widths[1:]) - len(_GAP) * (len(headers) - 1)
    widths[0] = min(widths[0], max(_MIN_NAME, budget))
    right = headers.index("Records")
    last = len(headers) - 1

    def line(cells: Sequence[_Cell]) -> str:
        parts = []
        for index, (text, colour) in enumerate(cells):
            text = _truncate(text, widths[index])
            if index == right:
                text = text.rjust(widths[index])
            elif index != last:
                text = text.ljust(widths[index])
            parts.append(_paint(text, colour, color))
        return _GAP.join(parts)

    lines = [
        f"Sources · checked {now:%Y-%m-%d %H:%M} UTC",
        "",
        line([(h, None) for h in headers]),
        _GAP.join("─" * w for w in widths),
    ]
    for status, cells in zip(statuses, rows, strict=True):
        lines.append(line(cells))
        latest = status.latest
        if latest is not None and latest.outcome is not Outcome.COMPLETED:
            note = f"{latest.outcome.value}: {_one_line(status.problem)}" if status.problem else ""
            lines.append(_paint(f"  ↳ {note or latest.outcome.value}", _RED, color))
        if status.last_load is not None:
            lines.append(_paint(f"  ↳ last load: {load_note(status.last_load)}", _DIM, color))
    lines += ["", _summary(statuses)]
    policy = _policy(statuses)
    if policy:
        lines.append(f"Data freshness policy: {policy}")
    if links is not None:
        lines.append(links_line(links, now))
    return "\n".join(lines) + "\n"


def links_line(links: NpiLinkSummary, now: datetime) -> str:
    """Where NPI linking stands: "NPI links: last run 3 h ago · 148,361 records · exact 23,100
    · high 4,210 · potential 19,870 · ambiguous 51,300 · none 49,881", and the records never
    checked when there are any."""
    run = links.last_run
    parts = [
        f"last run {ago(run.finished_at, now)}" if run else "never run",
        f"{links.records:,} records",
        *(f"{level.value} {links.by_best_level.get(level, 0):,}" for level in MatchLevel),
    ]
    if links.never_checked:
        parts.append(f"never checked {links.never_checked:,}")
    return "NPI links: " + " · ".join(parts)


def _row(status: SourceStatus, now: datetime) -> list[_Cell]:
    label, health_colour = _HEALTH[status.health]
    loaded = status.latest_completed is not None
    stale = status.freshness is Freshness.STALE
    overdue = status.health is SourceHealth.OVERDUE
    return [
        (status.display_name, None),
        (",".join(status.jurisdictions), None),
        (ago(status.latest.fetched_at, now) if status.latest else _EMPTY, None),
        (label, health_colour),
        (f"{status.current_records:,}" if loaded else _EMPTY, None),
        (_list_date(status, loaded=loaded), None),
        (status.freshness.value if status.freshness else _EMPTY, _YELLOW if stale else None),
        (until(status.next_due, now) if status.next_due else _EMPTY, _YELLOW if overdue else None),
    ]


def load_note(load: DatasetLoad) -> str:
    """A dataset load in a few words: added (+), changed (~), for a full file the missing rows
    (minus sign), then the wall-clock and CPU time: "+31,204 ~142,560 \N{MINUS SIGN}12 missing ·
    14 min · cpu 10 min"."""
    counts = [f"+{load.added:,}", f"~{load.changed:,}"]
    if load.mode is DatasetLoadMode.FULL:
        counts.append(f"\N{MINUS SIGN}{load.missing:,} missing")
    parts = [" ".join(counts), span(load.duration)]
    if load.cpu_time is not None:
        parts.append(f"cpu {span(load.cpu_time)}")
    return " · ".join(parts)


def _list_date(status: SourceStatus, *, loaded: bool) -> str:
    """The agency's own list date; "not stated" for a loaded list whose source gives none."""
    if status.data_as_of is not None:
        return status.data_as_of.date().isoformat()
    return _NOT_STATED if loaded else _EMPTY


def _summary(statuses: Sequence[SourceStatus]) -> str:
    def count(health: SourceHealth) -> int:
        return sum(1 for status in statuses if status.health is health)

    total = len(statuses)
    return " · ".join(
        (
            f"{total} source{'' if total == 1 else 's'}",
            f"{count(SourceHealth.OK)} ok",
            f"{count(SourceHealth.OVERDUE)} overdue",
            f"{count(SourceHealth.FAILING)} failing",
            f"{count(SourceHealth.NEVER_FETCHED)} never fetched",
        )
    )


def _policy(statuses: Sequence[SourceStatus]) -> str:
    """The freshness windows applied, per credential type, as the use case reported them."""
    windows: dict[CredentialType, set[timedelta]] = {}
    for status in statuses:
        windows.setdefault(status.credential_type, set()).add(status.freshness_max_age)
    return " · ".join(
        f"{_CREDENTIAL_LABELS[kind]} {' / '.join(span(age) for age in sorted(windows[kind]))}"
        for kind in CredentialType
        if kind in windows
    )


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def _one_line(text: str | None) -> str:
    return " ".join(text.split()) if text else ""


def _paint(text: str, colour: str | None, enabled: bool) -> str:
    return f"{colour}{text}{_RESET}" if enabled and colour else text
