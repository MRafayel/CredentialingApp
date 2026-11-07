from datetime import timedelta

from credentialing.cli.main import load_summary
from credentialing.cli.sources import load_note, render_table
from credentialing.domain.enums import DatasetLoadMode
from credentialing.domain.models import DatasetLoad
from tests.cli.test_sources import STATUSES
from tests.synthetic import NOW

FULL = DatasetLoad(
    mode=DatasetLoadMode.FULL,
    scanned=9_312_345,
    added=31_204,
    changed=142_560,
    unchanged=9_138_581,
    missing=12,
    phases={
        "download": timedelta(seconds=310),
        "scan": timedelta(seconds=71),
        "compare": timedelta(seconds=9),
        "read": timedelta(seconds=318),
        "apply": timedelta(seconds=134.3),
    },
    cpu_time=timedelta(seconds=611),
)


def test_the_sync_line_of_a_full_load_has_counts_times_and_phases() -> None:
    assert load_summary(FULL) == (
        "scanned=9,312,345 added=31,204 changed=142,560 unchanged=9,138,581 missing=12 "
        "duration=842.3s cpu=611.0s "
        "phases: download 310s scan 71s compare 9s read 318s apply 134s"
    )


def test_the_sync_line_of_a_deactivation_load_counts_deactivations_and_skips() -> None:
    load = DatasetLoad(
        mode=DatasetLoadMode.DEACTIVATIONS,
        scanned=360_014,
        added=3,
        changed=40,
        unchanged=359_970,
        outdated=1,
        deactivated=43,
        phases={"scan": timedelta(seconds=2)},
    )

    assert load_summary(load) == (
        "scanned=360,014 added=3 changed=40 unchanged=359,970 deactivated=43 outdated=1 "
        "duration=2.0s phases: scan 2s"
    )


def test_the_sources_table_notes_a_dataset_source_s_last_load() -> None:
    status = STATUSES[0].model_copy(update={"last_load": FULL})

    lines = render_table((status,), NOW, color=False).splitlines()

    row = next(index for index, line in enumerate(lines) if line.startswith("Fake fake_ok"))
    assert lines[row + 1] == (
        "  ↳ last load: +31,204 ~142,560 \N{MINUS SIGN}12 missing · 14 min · cpu 10 min"
    )


def test_an_incremental_load_note_has_no_missing_count() -> None:
    load = FULL.model_copy(update={"mode": DatasetLoadMode.INCREMENTAL, "cpu_time": None})

    assert load_note(load) == "+31,204 ~142,560 · 14 min"
