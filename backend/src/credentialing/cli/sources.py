"""Rendering of ``sources``: the source monitoring dashboard, as a table or as JSON.

A result is always a symbol and a word, so colour only ever repeats what the text says.
Colour is the caller's decision (a terminal, and ``NO_COLOR`` unset).
"""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, TextIO

from credentialing.application.source_status import SourceHealth, SourceStatus
from credentialing.cli.relative_time import ago, span, until
from credentialing.domain.enums import CredentialType, Freshness, Outcome

WIDTH: Final = 100
"""The table never gets wider than this; the source name column gives way."""

_GAP: Final = "  "
_MIN_NAME: Final = 12
_EMPTY: Final = "\N{EN DASH}"

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
) -> int:
    """Write the dashboard (or its JSON) to ``out`` and return the process exit code."""
    out.write(render_json(statuses) if as_json else render_table(statuses, now, color=color))
    return exit_code(statuses, strict=strict)


def exit_code(statuses: Sequence[SourceStatus], *, strict: bool) -> int:
    """0, or with ``strict`` 1 when any source is not ``OK``."""
    return 1 if strict and any(s.health is not SourceHealth.OK for s in statuses) else 0


def render_json(statuses: Sequence[SourceStatus]) -> str:
    """Every status with all its fields; timestamps and durations in ISO 8601."""
    payload = [status.model_dump(mode="json") for status in statuses]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_table(
    statuses: Sequence[SourceStatus], now: datetime, *, color: bool, width: int = WIDTH
) -> str:
    """One row per source, a note line under each failed fetch, and a summary."""
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
    lines += ["", _summary(statuses)]
    policy = _policy(statuses)
    if policy:
        lines.append(f"Data freshness policy: {policy}")
    return "\n".join(lines) + "\n"


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
        (status.data_as_of.date().isoformat() if status.data_as_of else _EMPTY, None),
        (status.freshness.value if status.freshness else _EMPTY, _YELLOW if stale else None),
        (until(status.next_due, now) if status.next_due else _EMPTY, _YELLOW if overdue else None),
    ]


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
