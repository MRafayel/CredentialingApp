import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest

from credentialing.connectors.framework.http import (
    FILE_CHUNK_BYTES,
    FetchedFile,
    FetchFailure,
    FileOutcome,
    HttpFetcher,
    NotModified,
)
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import SourceSnapshot
from tests.fakes import FixedClock
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

URL = "https://example.test/files/registry.zip"
BODY = b"PK\x03\x04 synthetic zip bytes " * 100
LAST_MODIFIED = "Tue, 03 Nov 2026 03:29:06 GMT"

type Handler = Callable[[httpx2.Request], httpx2.Response]


def _previous(*, content_hash: str | None = None, etag: str | None = None) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="test_dataset",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url=URL,
        content_hash=content_hash,
        etag=etag,
        last_modified=datetime(2026, 11, 3, 3, 29, 6, tzinfo=UTC),
    )


async def _download(
    handler: Handler,
    destination: Path,
    *,
    previous: SourceSnapshot | None = None,
    signal: ChangeSignal = ChangeSignal.CONTENT_HASH,
    max_file_bytes: int = 10 * 1024 * 1024,
    crawl_delay: timedelta = timedelta(0),
    sleeps: list[float] | None = None,
) -> FileOutcome:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport, follow_redirects=True) as client:
        fetcher = HttpFetcher(
            client,
            FixedClock(NOW),
            retry_pause_seconds=0,
            max_file_bytes=max_file_bytes,
            sleep=sleep,
        )
        return await fetcher.download(
            URL,
            destination=destination,
            previous=previous,
            change_signal=signal,
            crawl_delay=crawl_delay,
        )


def _leftovers(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.rglob("*") if path.is_file())


async def test_a_new_file_is_streamed_to_disk_with_its_hash_and_validators(
    tmp_path: Path,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        headers = {
            "content-type": "application/zip",
            "etag": '"v2"',
            "last-modified": LAST_MODIFIED,
        }
        return httpx2.Response(200, content=BODY, headers=headers)

    destination = tmp_path / "test_dataset" / "registry.zip"
    result = await _download(handler, destination)

    assert isinstance(result, FetchedFile)
    assert result.path == destination
    assert destination.read_bytes() == BODY
    assert result.content_hash == hashlib.sha256(BODY).hexdigest()
    assert result.byte_size == len(BODY)
    assert (result.etag, result.content_type, result.final_url) == ('"v2"', "application/zip", URL)
    assert result.last_modified == datetime(2026, 11, 3, 3, 29, 6, tzinfo=UTC)
    assert result.fetched_at == NOW
    assert _leftovers(tmp_path) == ["registry.zip"]


async def test_a_body_larger_than_a_chunk_is_written_whole(tmp_path: Path) -> None:
    body = bytes(range(256)) * (FILE_CHUNK_BYTES // 256 * 2 + 7)

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 65536):
            yield body[start : start + 65536]

    result = await _download(lambda _: httpx2.Response(200, content=chunks()), tmp_path / "f.zip")

    assert isinstance(result, FetchedFile)
    assert (tmp_path / "f.zip").read_bytes() == body
    assert result.content_hash == hashlib.sha256(body).hexdigest()


async def test_a_located_file_is_requested_conditionally_and_304_writes_nothing(
    tmp_path: Path,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(304)

    result = await _download(
        handler,
        tmp_path / "f.zip",
        previous=_previous(etag='"v1"'),
        signal=ChangeSignal.CONDITIONAL_GET,
    )

    assert isinstance(result, NotModified)
    assert result.reason == "HTTP 304"
    assert seen[0].headers["if-none-match"] == '"v1"'
    assert seen[0].headers["if-modified-since"] == LAST_MODIFIED
    assert _leftovers(tmp_path) == []


async def test_a_plain_download_sends_no_validators(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=BODY)

    await _download(handler, tmp_path / "f.zip", previous=_previous(etag='"v1"'))

    assert "if-none-match" not in seen[0].headers


async def test_a_file_hashing_as_before_is_unchanged_and_deleted(tmp_path: Path) -> None:
    previous = _previous(content_hash=hashlib.sha256(BODY).hexdigest())

    result = await _download(
        lambda _: httpx2.Response(200, content=BODY), tmp_path / "f.zip", previous=previous
    )

    assert isinstance(result, NotModified)
    assert result.reason == "content hash unchanged"
    assert _leftovers(tmp_path) == []


async def test_a_declared_size_over_the_cap_is_failed_before_reading(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=BODY, headers={"content-length": str(len(BODY))})

    result = await _download(handler, tmp_path / "f.zip", max_file_bytes=len(BODY) - 1)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert result.note == f"file of {len(BODY)} bytes exceeds the cap"
    assert _leftovers(tmp_path) == []


async def test_a_streamed_size_over_the_cap_is_failed_and_the_partial_file_deleted(
    tmp_path: Path,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 1000

    result = await _download(
        lambda _: httpx2.Response(200, content=chunks()), tmp_path / "f.zip", max_file_bytes=4500
    )

    assert isinstance(result, FetchFailure)
    assert result.note == "file exceeds the cap of 4500 bytes"
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize(
    ("status", "outcome", "note"),
    [
        (404, Outcome.FAILED, "unexpected HTTP 404"),
        (403, Outcome.BLOCKED, "HTTP 403"),
        (503, Outcome.SOURCE_UNAVAILABLE, "HTTP 503"),
    ],
)
async def test_an_error_status_is_its_outcome_and_writes_nothing(
    tmp_path: Path, status: int, outcome: Outcome, note: str
) -> None:
    result = await _download(
        lambda _: httpx2.Response(status, content=b"Not found."), tmp_path / "f.zip"
    )

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status, result.note) == (outcome, status, note)
    assert _leftovers(tmp_path) == []


async def test_a_challenge_page_is_blocked_not_a_file(tmp_path: Path) -> None:
    page = b"<html><head><title>Just a moment...</title></head></html>"

    result = await _download(
        lambda _: httpx2.Response(200, content=page, headers={"content-type": "text/html"}),
        tmp_path / "f.zip",
    )

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.BLOCKED
    assert _leftovers(tmp_path) == []


async def test_a_page_after_a_redirect_to_another_host_is_blocked(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "example.test":
            return httpx2.Response(302, headers={"location": "https://validate.example.org/"})
        return httpx2.Response(
            200, content=b"<html>check</html>", headers={"content-type": "text/html"}
        )

    result = await _download(handler, tmp_path / "f.zip")

    assert isinstance(result, FetchFailure)
    assert result.note == "redirected to validate.example.org"
    assert _leftovers(tmp_path) == []


async def test_a_transport_error_is_retried_once_from_the_start(tmp_path: Path) -> None:
    attempts: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx2.ConnectError("refused")
        return httpx2.Response(200, content=BODY)

    result = await _download(handler, tmp_path / "f.zip")

    assert isinstance(result, FetchedFile)
    assert len(attempts) == 2
    assert (tmp_path / "f.zip").read_bytes() == BODY


async def test_a_connection_lost_mid_file_leaves_no_partial_file(tmp_path: Path) -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield b"x" * 1000
        raise httpx2.ReadError("connection reset")

    result = await _download(lambda _: httpx2.Response(200, content=broken()), tmp_path / "f.zip")

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.SOURCE_UNAVAILABLE
    assert result.note == "ReadError after 2 attempts"
    assert _leftovers(tmp_path) == []


async def test_the_crawl_delay_is_waited_before_every_attempt(tmp_path: Path) -> None:
    sleeps: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("slow")

    await _download(handler, tmp_path / "f.zip", crawl_delay=timedelta(seconds=10), sleeps=sleeps)

    assert sleeps == [10.0, 0, 10.0]
