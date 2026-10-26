"""Composition root of the command line: ``python -m credentialing.cli sync oig_leie``.

``sync`` fetches public sources, stores a snapshot per fetch and updates their records. It
prints one line per source and exits with 1 when any source could not be read.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence

from credentialing.application.errors import UnknownSourceError
from credentialing.application.sync_source import SyncReport
from credentialing.bootstrap import runtime
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
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    return asyncio.run(_sync(settings, args.source_id, every=args.all, force=args.force))


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
