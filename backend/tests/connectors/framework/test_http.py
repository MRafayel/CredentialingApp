from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2
import pytest

from credentialing.connectors.framework.http import (
    RETRY_ATTEMPTS,
    FetchedDocument,
    FetchFailure,
    FetchOutcome,
    HttpFetcher,
    NotModified,
)
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import SourceSnapshot
from tests.fakes import FixedClock
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

URL = "https://example.test/list.csv"
BODY = b"NAME,CODE\r\nTestperson,A\r\n"
LAST_MODIFIED = "Fri, 09 Oct 2026 12:51:03 GMT"

type Handler = Callable[[httpx2.Request], httpx2.Response]


def _previous(
    *, content_hash: str | None = None, etag: str | None = None, modified: bool = False
) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url=URL,
        content_hash=content_hash,
        etag=etag,
        last_modified=datetime(2026, 10, 9, 12, 51, 3, tzinfo=UTC) if modified else None,
        byte_size=len(BODY),
        row_count=1,
    )


async def _fetch(
    handler: Handler,
    *,
    previous: SourceSnapshot | None = None,
    signal: ChangeSignal = ChangeSignal.CONTENT_HASH,
    max_body_bytes: int = 1024,
    crawl_delay: timedelta = timedelta(0),
    sleeps: list[float] | None = None,
) -> FetchOutcome:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport, follow_redirects=True) as client:
        fetcher = HttpFetcher(
            client,
            FixedClock(NOW),
            retry_pause_seconds=0,
            max_body_bytes=max_body_bytes,
            sleep=sleep,
        )
        return await fetcher.fetch(
            URL, previous=previous, change_signal=signal, crawl_delay=crawl_delay
        )


async def test_a_new_body_is_returned_with_hash_and_validators() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        headers = {"content-type": "text/csv", "etag": '"v2"', "last-modified": LAST_MODIFIED}
        return httpx2.Response(200, content=BODY, headers=headers)

    result = await _fetch(handler)

    assert isinstance(result, FetchedDocument)
    assert result.body == BODY
    assert result.byte_size == len(BODY)
    assert len(result.content_hash) == 64
    assert result.etag == '"v2"'
    assert result.last_modified == datetime(2026, 10, 9, 12, 51, 3, tzinfo=UTC)
    assert result.content_type == "text/csv"
    assert result.final_url == URL
    assert result.fetched_at == NOW


async def test_conditional_get_sends_validators_and_reads_304_as_unchanged() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(304)

    result = await _fetch(
        handler,
        previous=_previous(etag='"v1"', modified=True),
        signal=ChangeSignal.CONDITIONAL_GET,
    )

    assert isinstance(result, NotModified)
    assert result.reason == "HTTP 304"
    assert seen[0].headers["if-none-match"] == '"v1"'
    assert seen[0].headers["if-modified-since"] == LAST_MODIFIED


async def test_conditional_get_without_previous_downloads() -> None:
    result = await _fetch(
        lambda request: httpx2.Response(200, content=BODY), signal=ChangeSignal.CONDITIONAL_GET
    )

    assert isinstance(result, FetchedDocument)


async def test_an_unchanged_hash_is_not_modified() -> None:
    first = await _fetch(lambda request: httpx2.Response(200, content=BODY))
    assert isinstance(first, FetchedDocument)

    second = await _fetch(
        lambda request: httpx2.Response(200, content=BODY),
        previous=_previous(content_hash=first.content_hash),
    )

    assert isinstance(second, NotModified)
    assert second.reason == "content hash unchanged"


async def test_header_compare_skips_the_download_when_validators_match() -> None:
    methods: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        return httpx2.Response(200, headers={"etag": '"v1"'}, content=b"")

    result = await _fetch(
        handler, previous=_previous(etag='"v1"'), signal=ChangeSignal.HEADER_COMPARE
    )

    assert isinstance(result, NotModified)
    assert methods == ["HEAD"]


async def test_header_compare_downloads_when_validators_differ() -> None:
    methods: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        methods.append(request.method)
        body = b"" if request.method == "HEAD" else BODY
        return httpx2.Response(200, headers={"etag": '"v2"'}, content=body)

    result = await _fetch(
        handler, previous=_previous(etag='"v1"'), signal=ChangeSignal.HEADER_COMPARE
    )

    assert isinstance(result, FetchedDocument)
    assert methods == ["HEAD", "GET"]


@pytest.mark.parametrize(
    ("status", "headers", "outcome"),
    [
        (403, {"cf-mitigated": "challenge"}, Outcome.BLOCKED),
        (403, {}, Outcome.BLOCKED),
        (401, {}, Outcome.BLOCKED),
        (429, {"retry-after": "120"}, Outcome.BLOCKED),
        (503, {}, Outcome.SOURCE_UNAVAILABLE),
        (500, {}, Outcome.SOURCE_UNAVAILABLE),
        (404, {}, Outcome.FAILED),
    ],
)
async def test_error_statuses_map_to_outcomes(
    status: int, headers: dict[str, str], outcome: Outcome
) -> None:
    result = await _fetch(lambda request: httpx2.Response(status, headers=headers))

    assert isinstance(result, FetchFailure)
    assert result.outcome is outcome
    assert result.status == status
    assert str(status) in result.note


async def test_a_challenge_page_served_with_200_is_blocked() -> None:
    page = b"<html><head><title>Just a moment...</title></head><body>cf-chl-x</body></html>"

    result = await _fetch(
        lambda request: httpx2.Response(200, content=page, headers={"content-type": "text/html"})
    )

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.BLOCKED


async def test_timeouts_are_retried_once_then_unavailable() -> None:
    attempts: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(1)
        raise httpx2.ReadTimeout("timed out", request=request)

    result = await _fetch(handler)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.SOURCE_UNAVAILABLE
    assert result.status is None
    assert "ReadTimeout" in result.note
    assert len(attempts) == RETRY_ATTEMPTS + 1


async def test_a_retry_that_succeeds_returns_the_document() -> None:
    attempts: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx2.ConnectError("refused", request=request)
        return httpx2.Response(200, content=BODY)

    assert isinstance(await _fetch(handler), FetchedDocument)


async def test_a_declared_oversized_body_fails() -> None:
    result = await _fetch(lambda request: httpx2.Response(200, content=BODY), max_body_bytes=10)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert "exceeds the cap" in result.note


async def test_a_streamed_oversized_body_fails() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(4):
            yield b"x" * 8

    result = await _fetch(lambda request: httpx2.Response(200, content=chunks()), max_body_bytes=16)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED


async def test_a_304_to_an_unconditional_request_fails() -> None:
    result = await _fetch(lambda request: httpx2.Response(304))

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED


@pytest.mark.parametrize("signal", [ChangeSignal.LINK_DISCOVERY, ChangeSignal.API_METADATA])
async def test_resolved_urls_are_fetched_like_a_conditional_get(signal: ChangeSignal) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(304)

    result = await _fetch(handler, previous=_previous(etag='"v1"'), signal=signal)

    assert isinstance(result, NotModified)
    assert seen[0].headers["if-none-match"] == '"v1"'


def _redirecting(target: str, content_type: str) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "example.test":
            return httpx2.Response(302, headers={"location": target})
        return httpx2.Response(200, content=BODY, headers={"content-type": content_type})

    return handler


async def test_an_html_page_on_another_host_after_a_redirect_is_blocked() -> None:
    handler = _redirecting("https://validate.botcheck.test/?ssa=1", "text/html; charset=utf-8")

    result = await _fetch(handler)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.BLOCKED
    assert result.note == "redirected to validate.botcheck.test"


async def test_a_file_on_another_host_after_a_redirect_is_downloaded() -> None:
    target = "https://bucket.storage.test/list.csv?X-Signature=abc"

    result = await _fetch(_redirecting(target, "text/csv"))

    assert isinstance(result, FetchedDocument)
    assert result.final_url == target


async def test_an_html_page_on_the_same_host_is_not_a_redirect_block() -> None:
    page = b"<html><body>Landing page</body></html>"

    result = await _fetch(
        lambda request: httpx2.Response(200, content=page, headers={"content-type": "text/html"})
    )

    assert isinstance(result, FetchedDocument)


async def test_the_crawl_delay_is_waited_before_every_request() -> None:
    sleeps: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = b"" if request.method == "HEAD" else BODY
        return httpx2.Response(200, headers={"etag": '"v2"'}, content=body)

    result = await _fetch(
        handler,
        previous=_previous(etag='"v1"'),
        signal=ChangeSignal.HEADER_COMPARE,
        crawl_delay=timedelta(seconds=10),
        sleeps=sleeps,
    )

    assert isinstance(result, FetchedDocument)
    assert sleeps == [10.0, 10.0]


async def test_the_crawl_delay_is_waited_before_the_retry_too() -> None:
    sleeps: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    await _fetch(handler, crawl_delay=timedelta(seconds=5), sleeps=sleeps)

    assert sleeps == [5.0, 0, 5.0]


async def test_no_crawl_delay_means_no_wait() -> None:
    sleeps: list[float] = []

    await _fetch(lambda request: httpx2.Response(200, content=BODY), sleeps=sleeps)

    assert sleeps == []
