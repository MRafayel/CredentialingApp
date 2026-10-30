from datetime import date

from credentialing.domain.policies import pair_rewritten_rows
from tests.synthetic import make_npi

NPI = make_npi("100000001")
OTHER_NPI = make_npi("100000002")
EFFECTIVE = date(2024, 3, 1)


def test_one_vanished_and_one_new_row_with_the_same_npi_and_date_pair() -> None:
    pairs = pair_rewritten_rows(
        {"old": (NPI, EFFECTIVE), "gone": (OTHER_NPI, EFFECTIVE)},
        {"new": (NPI, EFFECTIVE), "fresh": (OTHER_NPI, date(2024, 3, 2))},
    )

    assert pairs == {"new": "old"}


def test_a_key_shared_by_two_rows_on_either_side_does_not_pair() -> None:
    assert (
        pair_rewritten_rows(
            {"old": (NPI, EFFECTIVE), "older": (NPI, EFFECTIVE)}, {"new": (NPI, EFFECTIVE)}
        )
        == {}
    )
    assert (
        pair_rewritten_rows(
            {"old": (NPI, EFFECTIVE)}, {"new": (NPI, EFFECTIVE), "newer": (NPI, EFFECTIVE)}
        )
        == {}
    )


def test_rows_without_an_npi_never_pair() -> None:
    assert pair_rewritten_rows({"old": (None, EFFECTIVE)}, {"new": (None, EFFECTIVE)}) == {}


def test_rows_without_an_effective_date_pair_by_npi() -> None:
    assert pair_rewritten_rows({"old": (NPI, None)}, {"new": (NPI, None)}) == {"new": "old"}
    assert pair_rewritten_rows({"old": (NPI, None)}, {"new": (NPI, EFFECTIVE)}) == {}
