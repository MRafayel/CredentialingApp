"""The base class of dataset connectors: a large file streamed to disk and loaded by difference.

A dataset (the NPPES registry: a gigabyte zipped, ten gigabytes of CSV) is too large to hold
in memory or to parse whole on every poll. :class:`DatasetConnectorBase` locates the file as a
bulk-list connector does (``framework/base.py``: one landing-page request when the link is
unchanged), streams it to ``<data_dir>/<source_id>/<file name>`` through the
:class:`~credentialing.connectors.framework.http.FileFetcher`, builds the snapshot and opens
the file as a :class:`~credentialing.domain.ports.Dataset`. Reading it is the loader's job,
in two passes (``domain/ports.py``), so a load parses only the rows that changed.

A file that cannot be opened (not a zip, a missing member, an unexpected header) is a
``FAILED`` snapshot "unreadable file: ..." and is deleted. The browser fallback does not
apply: the browser has no streaming download.
"""

import asyncio
import hashlib
import logging
import re
import time
from abc import abstractmethod
from pathlib import Path, PurePosixPath
from typing import ClassVar, Final
from urllib.parse import urlsplit

from credentialing.connectors.framework.base import Located, SourceConnectorBase
from credentialing.connectors.framework.http import (
    FetchedFile,
    FetchFailure,
    FileFetcher,
    FileOutcome,
)
from credentialing.domain.enums import DatasetLoadMode, Outcome
from credentialing.domain.errors import DatasetFormatError
from credentialing.domain.models import SourceSnapshot
from credentialing.domain.ports import Dataset, DatasetFetch

logger = logging.getLogger(__name__)

DEFAULT_DATASET_DIR: Final = Path("data/datasets")
"""Where downloaded dataset files are written, under the gitignored ``data/``."""

_FILE_NAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")


class DatasetConnectorBase(SourceConnectorBase):
    """Implements :class:`~credentialing.domain.ports.DatasetConnector` for downloaded files.

    A subclass declares its ``capabilities``, where its file is (as for a bulk list:
    ``download_url``, or ``landing_url`` and ``find_link``), its load ``mode``, and
    :meth:`open_dataset`.
    """

    mode: ClassVar[DatasetLoadMode]
    """What the file says about the mirror: a full picture, changes only, or deactivations."""

    def __init__(self, fetcher: FileFetcher, *, data_dir: Path = DEFAULT_DATASET_DIR) -> None:
        super().__init__(fetcher)
        self._files = fetcher
        self._data_dir = data_dir

    @abstractmethod
    def open_dataset(self, path: Path, snapshot: SourceSnapshot) -> Dataset:
        """Open the downloaded file at ``path`` for the loader; closing it deletes the file.

        Called in a worker thread. Any exception makes the fetch ``FAILED``; a
        :class:`~credentialing.domain.errors.DatasetFormatError` names what is wrong.
        """

    def destination(self, url: str) -> Path:
        """Where the file at ``url`` is written: the source's folder, the URL's file name (a
        name that is not a plain file name becomes ``download``)."""
        name = PurePosixPath(urlsplit(url).path).name
        if not _FILE_NAME.fullmatch(name):
            name = "download"
        return self._data_dir / self.capabilities.source_id / name

    async def fetch_dataset(self, previous: SourceSnapshot | None) -> DatasetFetch:
        """Fetch the file, using ``previous`` to skip the download when nothing changed."""
        started = time.perf_counter()
        source_id = self.capabilities.source_id
        location, fallback_url = await self._resolve(previous)
        result: FileOutcome = (
            await self._download_file(location, previous)
            if isinstance(location, Located)
            else location
        )
        snapshot = self._snapshot_of(result, location, previous, fallback_url)
        dataset: Dataset | None = None
        if isinstance(result, FetchedFile):
            snapshot, dataset = await asyncio.to_thread(self._open, result.path, snapshot)
        logger.info(
            "dataset fetch source_id=%s outcome=%s status=%s bytes=%s note=%r duration_ms=%d",
            source_id,
            snapshot.outcome.value,
            result.status,
            snapshot.byte_size,
            snapshot.note,
            (time.perf_counter() - started) * 1000,
        )
        return DatasetFetch(snapshot=snapshot, mode=self.mode, dataset=dataset)

    async def _download_file(
        self, location: Located, previous: SourceSnapshot | None
    ) -> FileOutcome:
        """Stream the located file to disk and check it against the API's MD5, if any."""
        destination = self.destination(location.url)
        result = await self._files.download(
            location.url,
            destination=destination,
            previous=previous,
            change_signal=self._signal_for(location, previous),
            crawl_delay=self.capabilities.crawl_delay,
        )
        if location.md5 is None or not isinstance(result, FetchedFile):
            return result
        if await asyncio.to_thread(_md5, result.path) == location.md5:
            return result
        await asyncio.to_thread(result.path.unlink, missing_ok=True)
        return FetchFailure(
            outcome=Outcome.FAILED,
            status=result.status,
            note="hash mismatch: the file is not the one the API describes",
            fetched_at=result.fetched_at,
        )

    def _open(self, path: Path, snapshot: SourceSnapshot) -> tuple[SourceSnapshot, Dataset | None]:
        try:
            return snapshot, self.open_dataset(path, snapshot)
        except Exception as error:  # any unreadable file is this source's FAILED outcome
            path.unlink(missing_ok=True)
            reason = str(error) if isinstance(error, DatasetFormatError) else type(error).__name__
            failed = snapshot.model_copy(
                update={
                    "outcome": Outcome.FAILED,
                    "content_hash": None,
                    "etag": None,
                    "last_modified": None,
                    "note": f"unreadable file: {reason}",
                }
            )
            return failed, None


def _md5(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, lambda: hashlib.md5(usedforsecurity=False)).hexdigest()
