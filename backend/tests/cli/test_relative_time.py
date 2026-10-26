from datetime import timedelta

import pytest

from credentialing.cli.relative_time import ago, span, until
from tests.synthetic import NOW


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (timedelta(0), "just now"),
        (timedelta(seconds=59), "just now"),
        (timedelta(seconds=-30), "just now"),
        (timedelta(minutes=1), "1 min ago"),
        (timedelta(minutes=35, seconds=50), "35 min ago"),
        (timedelta(minutes=59, seconds=59), "59 min ago"),
        (timedelta(hours=1), "1 h ago"),
        (timedelta(hours=3, minutes=59), "3 h ago"),
        (timedelta(hours=23, minutes=59), "23 h ago"),
        (timedelta(days=1), "yesterday"),
        (timedelta(days=1, hours=23), "yesterday"),
        (timedelta(days=2), "2 d ago"),
        (timedelta(days=4, hours=20), "4 d ago"),
        (timedelta(days=400), "400 d ago"),
    ],
)
def test_ago(elapsed: timedelta, expected: str) -> None:
    assert ago(NOW - elapsed, NOW) == expected


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        (timedelta(0), "due now"),
        (timedelta(seconds=59), "due now"),
        (timedelta(seconds=-59), "due now"),
        (timedelta(minutes=5), "in 5 min"),
        (timedelta(hours=18, minutes=30), "in 18 h"),
        (timedelta(days=6, hours=23), "in 6 d"),
        (timedelta(minutes=-1), "overdue 1 min"),
        (timedelta(hours=-3, minutes=-10), "overdue 3 h"),
        (timedelta(days=-2), "overdue 2 d"),
    ],
)
def test_until(remaining: timedelta, expected: str) -> None:
    assert until(NOW + remaining, NOW) == expected


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=45), "45 s"),
        (timedelta(minutes=90), "1 h"),
        (timedelta(days=7), "7 d"),
    ],
)
def test_span(delta: timedelta, expected: str) -> None:
    assert span(delta) == expected


def test_a_negative_span_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        span(timedelta(seconds=-1))
