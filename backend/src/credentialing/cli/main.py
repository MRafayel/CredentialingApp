"""Composition root of the command line: ``python -m credentialing.cli sync oig_leie``.

``sync`` fetches public sources, stores a snapshot per fetch and updates their records (a
dataset source, NPPES, loads its mirror and prints the load's counts, phase timings and CPU
time). It prints one line per source and exits with 1 when any source could not be read.
With ``--due`` it skips the sources whose poll interval has not elapsed since their last
completed fetch, which is what a cron job runs.

``sources`` shows every registered source: its links, its last fetch, how many records are
current, how old the data is and when the next fetch is due, then the NPI links line and the
live NPI registry's usage (requests in the last week, the last one). ``--json`` prints the
same as JSON; ``--strict`` exits with 1 unless every polled source is ok.

``link-npis`` links exclusion records to NPIs of the registry mirror: every latest record
whose links are missing or older than the newest mirror load (``--source`` narrows it to one
list), then prints the run's counts, phase timings and CPU time. A sync that changed rows runs
the same linking by itself and prints its line under the source's; a linking failure makes it
exit with 1 without undoing the sync.

``monitor`` rechecks the monitored rosters, one organization at a time: every provider
against every exclusion list and, with an NPI, the NPI mirror (never the live registry).
``--due`` skips providers rechecked within the monitoring interval (the cron entry);
``--source`` rechecks every provider against one list. It prints the run's counts, phase
timings and CPU time, and exits with 1 when any provider's recheck failed. A sync that changed
rows runs the same recheck for that list (an NPPES load: for the NPI mirror) and prints its
line.

``openapi`` prints the HTTP API's OpenAPI schema as JSON, without a server or a database;
the frontend's API types are generated from it.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from datetime import datetime

from credentialing.api.app import create_app
from credentialing.application.errors import UnknownSourceError
from credentialing.application.link_npis import CLI_TRIGGER
from credentialing.application.recheck_providers import CLI_TRIGGER as MONITOR_CLI_TRIGGER
from credentialing.application.recheck_providers import DUE_TRIGGER
from credentialing.application.sync_source import SyncReport
from credentialing.bootstrap import runtime
from credentialing.cli.relative_time import until
from credentialing.cli.sources import show
from credentialing.domain.enums import DatasetLoadMode, MatchLevel, Outcome
from credentialing.domain.models import DatasetLoad, MonitoringRun, NpiLinkRun
from credentialing.domain.ports import BulkListConnector
from credentialing.infrastructure.config import Settings, get_settings
from credentialing.infrastructure.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line, run it and return the process exit code."""
    parser = argparse.ArgumentParser(prog="credentialing", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="fetch public sources and update their records")
    which = sync.add_mutually_exclusive_group(required=True)
    which.add_argument("source_id", nargs="?", help="a registered source id, such as oig_leie")
    which.add_argument(
        "--all", action="store_true", help="every registered bulk-list and dataset source"
    )
    sync.add_argument(
        "--force", action="store_true", help="download and parse even if the source is unchanged"
    )
    sync.add_argument(
        "--due",
        action="store_true",
        help="skip sources whose poll interval has not elapsed since their last completed fetch",
    )
    sources = commands.add_parser(
        "sources", help="every source's links, last fetch, data age and next fetch"
    )
    sources.add_argument(
        "--json", action="store_true", dest="as_json", help="machine-readable output"
    )
    sources.add_argument(
        "--strict",
        action="store_true",
        help="exit with 1 when any source is failing, overdue or never fetched",
    )
    link = commands.add_parser(
        "link-npis", help="link exclusion records to NPIs of the registry mirror"
    )
    link.add_argument("--source", help="only this list's records, such as ny_omig")
    monitor = commands.add_parser(
        "monitor", help="recheck every organization's providers against the lists and NPPES"
    )
    scope = monitor.add_mutually_exclusive_group()
    scope.add_argument(
        "--due", action="store_true", help="only providers whose recheck is due (for cron)"
    )
    scope.add_argument("--source", help="every provider against this list only, such as ny_omig")
    commands.add_parser("openapi", help="print the HTTP API's OpenAPI schema as JSON")
    args = parser.parse_args(argv)

    if args.command == "openapi":
        sys.stdout.write(openapi_json())
        return 0
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "sources":
        return asyncio.run(_sources(settings, as_json=args.as_json, strict=args.strict))
    if args.command == "link-npis":
        return asyncio.run(_link_npis(settings, args.source))
    if args.command == "monitor":
        return asyncio.run(_monitor(settings, due=args.due, source_id=args.source))
    return asyncio.run(
        _sync(settings, args.source_id, every=args.all, force=args.force, only_due=args.due)
    )


def openapi_json() -> str:
    """The API's OpenAPI schema, built from the routes alone (no lifespan, no database)."""
    schema = create_app(expose_docs=True).openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


async def _sources(settings: Settings, *, as_json: bool, strict: bool) -> int:
    async with runtime(settings) as services:
        now = services.clock.now()
        statuses = await services.source_status.run()
        links = await services.link_npis.summary()
        api = await services.source_status.npi_api_usage()
        monitoring = await services.recheck_providers.summary()
    color = not as_json and not settings.no_color and sys.stdout.isatty()
    return show(
        statuses,
        now,
        out=sys.stdout,
        as_json=as_json,
        strict=strict,
        color=color,
        links=links,
        api=api,
        monitoring=monitoring,
    )


async def _monitor(settings: Settings, *, due: bool, source_id: str | None) -> int:
    async with runtime(settings) as services:
        try:
            run = await services.recheck_providers.run(
                trigger=DUE_TRIGGER if due else MONITOR_CLI_TRIGGER,
                source_id=source_id,
                due_only=due,
            )
        except UnknownSourceError:
            sys.stderr.write(f"no exclusion list is registered as {source_id!r}\n")
            return 2
    sys.stdout.write(monitor_summary(run) + "\n")
    return 1 if run.failures else 0


async def _link_npis(settings: Settings, source_id: str | None) -> int:
    async with runtime(settings) as services:
        if source_id is not None and not isinstance(
            services.registry.get(source_id), BulkListConnector
        ):
            sys.stderr.write(f"no bulk-list source is registered as {source_id!r}\n")
            return 2
        run = await services.link_npis.run(source_id=source_id, trigger=CLI_TRIGGER)
    sys.stdout.write(link_summary(run) + "\n")
    return 0


async def _sync(
    settings: Settings, source_id: str | None, *, every: bool, force: bool, only_due: bool
) -> int:
    async with runtime(settings) as services:
        if every:
            source_ids = [c.capabilities.source_id for c in services.registry.polled_connectors()]
        else:
            source_ids = [source_id] if source_id else []
        failed = False
        for current in source_ids:
            started = time.perf_counter()
            try:
                due_at = await services.sync_source.next_due(current) if only_due else None
                if due_at is not None and due_at > (now := services.clock.now()):
                    sys.stdout.write(skipped_line(current, due_at, now) + "\n")
                    continue
                report = await services.sync_source.run(current, force=force)
            except UnknownSourceError as error:
                sys.stderr.write(f"{error}\n")
                return 2
            sys.stdout.write(_line(current, report, time.perf_counter() - started) + "\n")
            if report.linking is not None:
                sys.stdout.write(f"{current} npi links: {link_summary(report.linking)}\n")
            if report.linking_failed is not None:
                sys.stdout.write(f"{current} {report.linking_failed}\n")
            if report.recheck is not None:
                sys.stdout.write(f"{current} recheck: {monitor_summary(report.recheck)}\n")
            if report.recheck_failed is not None:
                sys.stdout.write(f"{current} {report.recheck_failed}\n")
            failed = (
                failed
                or report.snapshot.outcome is not Outcome.COMPLETED
                or report.linking_failed is not None
                or report.recheck_failed is not None
            )
        return 1 if failed else 0


def skipped_line(source_id: str, due_at: datetime, now: datetime) -> str:
    """The line ``sync --due`` prints for a source that is not due yet."""
    return f"{source_id} skipped: next due {until(due_at, now)}"


def _line(source_id: str, report: SyncReport, seconds: float) -> str:
    snapshot = report.snapshot
    diff = report.diff
    note = f" ({snapshot.note})" if snapshot.note else ""
    if report.load is not None:
        return f"{source_id} {snapshot.outcome.value} {load_summary(report.load)}{note}"
    counts = (
        f"added={diff.added} readded={diff.readded} changed={diff.changed} "
        f"rewritten={diff.rewritten} removed={diff.removed} reparsed={diff.reparsed} "
        f"unchanged={diff.unchanged}"
        if diff
        else "no new list"
    )
    return f"{source_id} {snapshot.outcome.value} {counts} duration={seconds:.1f}s{note}"


def load_summary(load: DatasetLoad) -> str:
    """A dataset load on one line: counts, total and CPU time, then each phase."""
    counts = [
        f"scanned={load.scanned:,}",
        f"added={load.added:,}",
        f"changed={load.changed:,}",
        f"unchanged={load.unchanged:,}",
    ]
    if load.mode is DatasetLoadMode.FULL:
        counts.append(f"missing={load.missing:,}")
    if load.mode is DatasetLoadMode.DEACTIVATIONS:
        counts.append(f"deactivated={load.deactivated:,}")
    if load.outdated:
        counts.append(f"outdated={load.outdated:,}")
    if load.duplicates:
        counts.append(f"duplicates={load.duplicates:,}")
    counts.append(f"duration={load.duration.total_seconds():.1f}s")
    if load.cpu_time is not None:
        counts.append(f"cpu={load.cpu_time.total_seconds():.1f}s")
    phases = " ".join(f"{name} {spent.total_seconds():.0f}s" for name, spent in load.phases.items())
    return " ".join(counts) + (f" phases: {phases}" if phases else "")


def link_summary(run: NpiLinkRun) -> str:
    """A linking run on one line: records by best level, total and CPU time, then each phase:
    "linked 148,361 records: exact=23,100 high=4,210 potential=19,870 ambiguous=51,300
    none=49,881 duration=183.2s cpu=120.4s phases: candidates 60s scoring 95s writing 28s"."""
    if run.note:
        return f"linked {run.records:,} records ({run.note})"
    counts = " ".join(f"{level.value}={run.by_level.get(level, 0):,}" for level in MatchLevel)
    parts = [f"linked {run.records:,} records: {counts}"]
    parts.append(f"duration={run.duration.total_seconds():.1f}s")
    if run.cpu_time is not None:
        parts.append(f"cpu={run.cpu_time.total_seconds():.1f}s")
    phases = " ".join(f"{name} {spent.total_seconds():.0f}s" for name, spent in run.phases.items())
    return " ".join(parts) + (f" phases: {phases}" if phases else "")


def monitor_summary(run: MonitoringRun) -> str:
    """A recheck run on one line: "rechecked 1,200 providers in 3 organizations: results=36,000
    changed=4 failures=0 duration=95.3s cpu=60.1s phases: lists 0s providers 1s exclusions 80s
    npi 14s", or the note when it checked nothing; "purged=310" (history rows past the
    retention window) when it purged any."""
    scope = f" against {run.source_id}" if run.source_id else ""
    if run.note:
        purged = f", purged={run.purged:,}" if run.purged else ""
        return f"rechecked {run.providers:,} providers{scope} ({run.note}{purged})"
    parts = [
        f"rechecked {run.providers:,} providers{scope} in {run.organizations:,} "
        f"organization{'' if run.organizations == 1 else 's'}:",
        f"results={run.results:,}",
        f"changed={run.changed:,}",
        f"failures={run.failures:,}",
        *([f"purged={run.purged:,}"] if run.purged else []),
        f"duration={run.duration.total_seconds():.1f}s",
    ]
    if run.cpu_time is not None:
        parts.append(f"cpu={run.cpu_time.total_seconds():.1f}s")
    phases = " ".join(f"{name} {spent.total_seconds():.0f}s" for name, spent in run.phases.items())
    return " ".join(parts) + (f" phases: {phases}" if phases else "")


if __name__ == "__main__":
    raise SystemExit(main())
