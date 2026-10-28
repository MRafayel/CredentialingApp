from datetime import timedelta

from credentialing.cli.main import skipped_line
from tests.synthetic import NOW


def test_a_skipped_source_says_when_it_is_due() -> None:
    line = skipped_line("oig_leie", NOW + timedelta(days=5, hours=3), NOW)

    assert line == "oig_leie skipped: next due in 5 d"


def test_a_source_due_within_the_hour_says_so() -> None:
    assert skipped_line("ny_omig", NOW + timedelta(minutes=40), NOW) == (
        "ny_omig skipped: next due in 40 min"
    )
