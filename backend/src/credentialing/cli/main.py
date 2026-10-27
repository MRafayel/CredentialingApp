"""Composition root of the command line: ``python -m credentialing.cli sync oig_leie``.

``sync`` fetches public sources, stores a snapshot per fetch and updates their records. It
prints one line per source and exits with 1 when any source could not be read.

``sources`` shows every registered source: its links, its last fetch, how many records are
current, how old the data is and when the next fetch is due. ``--json`` prints the same as
JSON; ``--strict`` exits with 1 unless every source is ok.

``openapi`` prints the HTTP API's OpenAPI schema as JSON, without a server or a database;
the frontend's API types are generated from it.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence

from credentialing.api.app import create_app
from credentialing.application.errors import UnknownSourceError
from credentialing.application.sync_source import SyncReport
from credentialing.bootstrap import runtime
from credentialing.cli.sources import show
from credentialing.domain.enums import Outcome
from credentialing.infrastructure.config import Settings, get_settings
from credentialing.infrastructure.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line, run it and return the process exit code."""
    parser = argparse.ArgumentParser(prog="credentialing", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="fetch public sources and update their records")
    which = sync.add_mutually_exclusive_group(required=True)
    which.add_argument("source_id", nargs="?", help="a registered source id, such as oig_leie")
    which.add_argument("--all", action="store_true", help="every registered bulk-list source")
    sync.add_argument(
        "--force", action="store_true", help="download and parse even if the source is unchanged"
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
    commands.add_parser("openapi", help="print the HTTP API's OpenAPI schema as JSON")
    args = parser.parse_args(argv)

    if args.command == "openapi":
        sys.stdout.write(openapi_json())
        return 0
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "sources":
        return asyncio.run(_sources(settings, as_json=args.as_json, strict=args.strict))
    return asyncio.run(_sync(settings, args.source_id, every=args.all, force=args.force))


def openapi_json() -> str:
    """The API's OpenAPI schema, built from the routes alone (no lifespan, no database)."""
    schema = create_app(expose_docs=True).openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


async def _sources(settings: Settings, *, as_json: bool, strict: bool) -> int:
    async with runtime(settings) as services:
        now = services.clock.now()
        statuses = await services.source_status.run()
    color = not as_json and not settings.no_color and sys.stdout.isatty()
    return show(statuses, now, out=sys.stdout, as_json=as_json, strict=strict, color=color)


async def _sync(settings: Settings, source_id: str | None, *, every: bool, force: bool) -> int:
    async with runtime(settings) as services:
        if every:
            source_ids = [c.capabilities.source_id for c in services.registry.bulk_connectors()]
        else:
            source_ids = [source_id] if source_id else []
        failed = False
        for current in source_ids:
            started = time.perf_counter()
            try:
                report = await services.sync_source.run(current, force=force)
            except UnknownSourceError as error:
                sys.stderr.write(f"{error}\n")
                return 2
            sys.stdout.write(_line(current, report, time.perf_counter() - started) + "\n")
            failed = failed or report.snapshot.outcome is not Outcome.COMPLETED
        return 1 if failed else 0


def _line(source_id: str, report: SyncReport, seconds: float) -> str:
    snapshot = report.snapshot
    diff = report.diff
    counts = (
        f"added={diff.added} changed={diff.changed} removed={diff.removed} "
        f"unchanged={diff.unchanged}"
        if diff
        else "no new list"
    )
    note = f" ({snapshot.note})" if snapshot.note else ""
    return f"{source_id} {snapshot.outcome.value} {counts} duration={seconds:.1f}s{note}"


if __name__ == "__main__":
    raise SystemExit(main())
