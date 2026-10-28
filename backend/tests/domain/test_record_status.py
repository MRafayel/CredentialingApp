from datetime import date

import pytest

from credentialing.domain.enums import ExclusionAction, RecordStatus
from credentialing.domain.policies import record_has_ended, record_status
from tests.synthetic import exclusion

TODAY = date(2026, 10, 27)


@pytest.mark.parametrize(
    ("end_date", "ended"),
    [
        (None, False),
        (date(2026, 10, 26), True),
        (date(2026, 10, 27), False),
        (date(2031, 6, 23), False),
    ],
)
def test_a_record_has_ended_once_its_end_date_has_passed(
    end_date: date | None, ended: bool
) -> None:
    assert record_has_ended(exclusion(end_date=end_date), TODAY) is ended


def test_a_reinstatement_row_has_ended_whatever_its_dates() -> None:
    reinstated = exclusion(action=ExclusionAction.REINSTATEMENT, end_date=None)

    assert record_has_ended(reinstated, TODAY)


def test_ended_only_when_every_matched_record_has_ended() -> None:
    ended = exclusion(end_date=date(2020, 1, 1))
    standing = exclusion(end_date=None, last="Testperson", first="Avery")

    assert record_status([ended], TODAY) is RecordStatus.ENDED
    assert record_status([ended, standing], TODAY) is RecordStatus.ACTIVE
    assert record_status([], TODAY) is RecordStatus.ACTIVE
