"""Turning a fetch outcome into the :class:`SourceSnapshot` stored for every poll."""

from uuid import uuid4

from credentialing.connectors.framework.http import (
    FetchedDocument,
    FetchFailure,
    FetchOutcome,
    NotModified,
)
from credentialing.domain.enums import Outcome
from credentialing.domain.models import SourceSnapshot


def snapshot_from(
    result: FetchOutcome, *, source_id: str, url: str, previous: SourceSnapshot | None
) -> SourceSnapshot:
    """Build the snapshot of one fetch.

    A new document is ``COMPLETED`` with its hash and validators (the row count is added after
    parsing). An unchanged source is ``COMPLETED`` with the note "unchanged" and keeps the
    previous hash, validators, size and row count, so the next poll stays a single request.
    A failure keeps no hash or validators, so the next poll downloads the source again.
    """
    match result:
        case FetchedDocument():
            return SourceSnapshot(
                id=uuid4(),
                source_id=source_id,
                fetched_at=result.fetched_at,
                outcome=Outcome.COMPLETED,
                source_url=result.final_url,
                content_hash=result.content_hash,
                etag=result.etag,
                last_modified=result.last_modified,
                byte_size=result.byte_size,
            )
        case NotModified():
            if previous is None:
                raise ValueError("an unchanged source needs the previous snapshot")
            return SourceSnapshot(
                id=uuid4(),
                source_id=source_id,
                fetched_at=result.fetched_at,
                outcome=Outcome.COMPLETED,
                source_url=url,
                content_hash=previous.content_hash,
                etag=result.etag or previous.etag,
                last_modified=result.last_modified or previous.last_modified,
                byte_size=previous.byte_size,
                row_count=previous.row_count,
                note=f"unchanged ({result.reason})",
            )
        case FetchFailure():
            return SourceSnapshot(
                id=uuid4(),
                source_id=source_id,
                fetched_at=result.fetched_at,
                outcome=result.outcome,
                source_url=url,
                note=result.note,
            )
