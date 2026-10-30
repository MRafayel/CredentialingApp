from datetime import UTC, date, datetime

import pytest

from credentialing.domain.enums import ExclusionAction, RecordStatus
from credentialing.domain.models import ExclusionRecord
from credentialing.domain.policies import record_has_ended, record_is_removed, record_status
from tests.synthetic import exclusion

TODAY = date(2026, 10, 27)
REMOVED_AT = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


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


def _removed(effective_date: date, end_date: date | None = None) -> ExclusionRecord:
    """A record whose newest version was closed: it left its published list."""
    record = exclusion(effective_date=effective_date, end_date=end_date)
    return record.model_copy(update={"valid_to": REMOVED_AT})


def test_a_record_is_removed_once_its_newest_version_was_closed() -> None:
    assert record_is_removed(_removed(date(2021, 1, 1)))
    assert not record_is_removed(exclusion())


def test_record_status_puts_ended_before_removed_before_active() -> None:
    standing = exclusion(effective_date=date(2023, 1, 1))
    ended = exclusion(effective_date=date(2015, 1, 1), end_date=date(2020, 1, 1))
    removed = _removed(date(2021, 1, 1))
    removed_and_ended = _removed(date(2016, 1, 1), date(2018, 1, 1))

    assert record_status([removed], TODAY) is RecordStatus.REMOVED
    assert record_status([removed, ended], TODAY) is RecordStatus.REMOVED
    assert record_status([removed_and_ended], TODAY) is RecordStatus.ENDED
    assert record_status([removed_and_ended, ended], TODAY) is RecordStatus.ENDED
    assert record_status([removed, standing], TODAY) is RecordStatus.ACTIVE
    assert record_status([removed, ended, standing], TODAY) is RecordStatus.ACTIVE
