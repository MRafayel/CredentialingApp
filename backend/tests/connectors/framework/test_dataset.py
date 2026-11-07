import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import pytest

from credentialing.connectors.framework.base import Metadata, first_href
from credentialing.connectors.framework.dataset import DatasetConnectorBase
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure
from credentialing.domain.enums import ChangeSignal, DatasetLoadMode, Outcome
from credentialing.domain.errors import DatasetFormatError
from credentialing.domain.models import ConnectorCapabilities, SourceSnapshot
from credentialing.domain.ports import BulkListConnector, Dataset, DatasetConnector
from tests.fakes import (
    FILE_FETCHED_AT,
    FILE_LAST_MODIFIED,
    ListDataset,
    ScriptedFetcher,
    dataset_capabilities,
)
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

LANDING = "https://example.test/files.html"
FILE_URL = "https://example.test/files/registry_2026_01.zip"
CAPABILITIES = dataset_capabilities("test_dataset").model_copy(
    update={"crawl_delay": timedelta(seconds=3)}
)


class _Connector(DatasetConnectorBase):
    landing_url: ClassVar[str | None] = LANDING
    mode = DatasetLoadMode.FULL

    def __init__(self, fetcher: ScriptedFetcher, data_dir: Path) -> None:
        super().__init__(fetcher, data_dir=data_dir)
        self.opened: list[tuple[Path, bytes]] = []

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return CAPABILITIES

    def find_link(self, html: str) -> str:
        return first_href(html, re.compile(r"registry_\d{4}_\d{2}\.zip$"))

    def open_dataset(self, path: Path, snapshot: SourceSnapshot) -> Dataset:
        body = path.read_bytes()
        self.opened.append((path, body))
        if not body.startswith(b"PK"):
            raise DatasetFormatError("not a zip archive (BadZipFile)")
        return ListDataset()


def _page(body: bytes = b"<a href='files/registry_2026_01.zip'>January</a>") -> FetchedDocument:
    return FetchedDocument(
        body=body,
        content_hash=hashlib.sha256(body).hexdigest(),
        etag=None,
        last_modified=None,
        content_type="text/html",
        final_url=LANDING,
        byte_size=len(body),
        fetched_at=NOW,
        status=200,
    )


def _completed(url: str = FILE_URL) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="test_dataset",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url=url,
        content_hash="ab" * 32,
        etag='"file-v0"',
        byte_size=10,
        row_count=3,
    )


def test_the_base_satisfies_the_dataset_connector_port_and_not_the_bulk_one(
    tmp_path: Path,
) -> None:
    connector = _Connector(ScriptedFetcher(), tmp_path)

    assert isinstance(connector, DatasetConnector)
    assert not isinstance(connector, BulkListConnector)


async def test_a_new_link_is_streamed_to_the_source_folder_and_opened(tmp_path: Path) -> None:
    fetcher = ScriptedFetcher(_page(), files=[b"PK new file"])
    connector = _Connector(fetcher, tmp_path)

    fetched = await connector.fetch_dataset(None)

    destination = tmp_path / "test_dataset" / "registry_2026_01.zip"
    assert fetched.snapshot.outcome is Outcome.COMPLETED
    assert fetched.snapshot.source_url == FILE_URL
    assert fetched.snapshot.content_hash == hashlib.sha256(b"PK new file").hexdigest()
    assert fetched.snapshot.etag == '"file-v1"'
    assert fetched.snapshot.last_modified == FILE_LAST_MODIFIED
    assert fetched.snapshot.fetched_at == FILE_FETCHED_AT
    assert fetched.snapshot.byte_size == len(b"PK new file")
    assert fetched.snapshot.row_count is None
    assert fetched.mode is DatasetLoadMode.FULL
    assert isinstance(fetched.dataset, ListDataset)
    assert connector.opened == [(destination, b"PK new file")]
    (request,) = fetcher.downloads
    assert (request.url, request.change_signal, request.destination) == (
        FILE_URL,
        ChangeSignal.CONTENT_HASH,
        destination,
    )
    assert request.crawl_delay == timedelta(seconds=3)
    assert fetcher.requests == [(LANDING, ChangeSignal.CONTENT_HASH)]


async def test_an_unchanged_link_is_one_request_and_no_download(tmp_path: Path) -> None:
    fetcher = ScriptedFetcher(_page(), files=[b"PK never"])
    previous = _completed()

    fetched = await _Connector(fetcher, tmp_path).fetch_dataset(previous)

    assert fetched.snapshot.outcome is Outcome.COMPLETED
    assert fetched.snapshot.note == "unchanged (link unchanged)"
    assert fetched.snapshot.source_url == FILE_URL
    assert fetched.snapshot.content_hash == previous.content_hash
    assert fetched.dataset is None
    assert fetcher.downloads == []
    assert len(fetcher.requests) == 1


async def test_the_same_link_after_a_failed_fetch_is_downloaded_conditionally(
    tmp_path: Path,
) -> None:
    fetcher = ScriptedFetcher(_page(), files=[b"PK again"])
    failed = _completed().model_copy(update={"outcome": Outcome.FAILED})

    fetched = await _Connector(fetcher, tmp_path).fetch_dataset(failed)

    assert fetched.dataset is not None
    assert fetcher.downloads[0].change_signal is ChangeSignal.CONDITIONAL_GET


async def test_a_missing_file_is_failed_without_a_dataset(tmp_path: Path) -> None:
    missing = FetchFailure(
        outcome=Outcome.FAILED, status=404, note="unexpected HTTP 404", fetched_at=NOW
    )

    fetched = await _Connector(ScriptedFetcher(_page(), files=[missing]), tmp_path).fetch_dataset(
        None
    )

    assert fetched.snapshot.outcome is Outcome.FAILED
    assert fetched.snapshot.note == "unexpected HTTP 404"
    assert fetched.snapshot.source_url == FILE_URL
    assert fetched.dataset is None


async def test_an_unreadable_file_is_failed_and_deleted(tmp_path: Path) -> None:
    connector = _Connector(ScriptedFetcher(_page(), files=[b"Not found."]), tmp_path)

    fetched = await connector.fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.FAILED
    assert fetched.snapshot.note == "unreadable file: not a zip archive (BadZipFile)"
    assert (fetched.snapshot.content_hash, fetched.snapshot.etag) == (None, None)
    assert fetched.dataset is None
    assert not (tmp_path / "test_dataset" / "registry_2026_01.zip").exists()


@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.SOURCE_UNAVAILABLE])
async def test_a_landing_page_failure_keeps_its_outcome(tmp_path: Path, outcome: Outcome) -> None:
    failure = FetchFailure(outcome=outcome, status=None, note="synthetic", fetched_at=NOW)
    fetcher = ScriptedFetcher(failure, files=[b"PK never"])

    fetched = await _Connector(fetcher, tmp_path).fetch_dataset(None)

    assert fetched.snapshot.outcome is outcome
    assert fetched.snapshot.source_url == LANDING
    assert fetched.dataset is None
    assert fetcher.downloads == []


async def test_a_landing_page_without_the_link_is_failed(tmp_path: Path) -> None:
    fetched = await _Connector(
        ScriptedFetcher(_page(b"<p>maintenance</p>"), files=[b"PK"]), tmp_path
    ).fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.FAILED
    assert fetched.snapshot.note is not None
    assert fetched.snapshot.note.startswith("no download link on the landing page")


def test_a_url_without_a_plain_file_name_is_saved_as_download(tmp_path: Path) -> None:
    connector = _Connector(ScriptedFetcher(), tmp_path)

    assert connector.destination("https://example.test/a/..") == (
        tmp_path / "test_dataset" / "download"
    )
    assert connector.destination("https://example.test/get?id=3") == (
        tmp_path / "test_dataset" / "get"
    )


class _ApiConnector(_Connector):
    landing_url = None
    metadata_url = "https://example.test/api/file"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return CAPABILITIES.model_copy(update={"change_signal": ChangeSignal.API_METADATA})

    def read_metadata(self, body: bytes) -> Metadata:
        answer = json.loads(body)
        return Metadata(url=answer["url"], hash=answer["md5"])


async def test_a_file_that_does_not_match_the_api_md5_is_failed_and_deleted(
    tmp_path: Path,
) -> None:
    answer = _page(json.dumps({"url": FILE_URL, "md5": "0" * 32}).encode())
    connector = _ApiConnector(ScriptedFetcher(answer, files=[b"PK body"]), tmp_path)

    fetched = await connector.fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.FAILED
    assert fetched.snapshot.note == "hash mismatch: the file is not the one the API describes"
    assert not (tmp_path / "test_dataset" / "registry_2026_01.zip").exists()


async def test_a_file_matching_the_api_md5_keeps_it_as_the_etag(tmp_path: Path) -> None:
    md5 = hashlib.md5(b"PK body", usedforsecurity=False).hexdigest()
    answer = _page(json.dumps({"url": FILE_URL, "md5": md5}).encode())

    fetched = await _ApiConnector(
        ScriptedFetcher(answer, files=[b"PK body"]), tmp_path
    ).fetch_dataset(None)

    assert fetched.snapshot.outcome is Outcome.COMPLETED
    assert fetched.snapshot.etag == md5
    assert fetched.dataset is not None
