import json
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
    Response,
    document_from,
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


# --- sessions: several requests sharing one cookie jar (token flows) ------------------------

PAGE = "https://example.test/page/"
EXPORT = "https://example.test/export/1"


def _session_fetcher(
    handler: Handler, *, sleeps: list[float] | None = None
) -> tuple[HttpFetcher, list[httpx2.AsyncClient]]:
    """A fetcher whose sessions run on new clients over ``handler``; the clients are kept."""
    created: list[httpx2.AsyncClient] = []

    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    def session_client() -> httpx2.AsyncClient:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), follow_redirects=True)
        created.append(client)
        return client

    shared = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), follow_redirects=True)
    fetcher = HttpFetcher(
        shared, FixedClock(NOW), session_client=session_client, retry_pause_seconds=0, sleep=sleep
    )
    return fetcher, created


async def test_a_session_carries_its_cookies_and_posts_json() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path == "/page/":
            return httpx2.Response(
                200, content=b"<html>token</html>", headers={"set-cookie": "sid=s1; Path=/"}
            )
        return httpx2.Response(200, json={"success": True})

    fetcher, created = _session_fetcher(handler)
    body = {"viewName": "Mock", "page": 1, "search": None}

    async with fetcher.session(crawl_delay=timedelta(0)) as session:
        page = await session.request("GET", PAGE, headers={"X-Requested-With": "XMLHttpRequest"})
        answer = await session.request(
            "POST",
            EXPORT,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            json=body,
        )
        file = await session.request("GET", EXPORT, params={"key": "k|1"})

    assert isinstance(page, Response)
    assert isinstance(answer, Response)
    assert isinstance(file, Response)
    assert seen[0].headers["x-requested-with"] == "XMLHttpRequest"
    assert "cookie" not in seen[0].headers
    assert seen[1].method == "POST"
    assert seen[1].headers["cookie"] == "sid=s1"
    assert seen[1].headers["content-type"] == "application/json; charset=UTF-8"
    assert seen[1].content == json.dumps(body).encode()
    assert seen[2].headers["cookie"] == "sid=s1"
    assert seen[2].url.params["key"] == "k|1"
    assert answer.body == b'{"success":true}'
    assert [client.is_closed for client in created] == [True]


async def test_json_without_a_content_type_is_sent_as_json() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"{}")

    fetcher, _ = _session_fetcher(handler)
    async with fetcher.session(crawl_delay=timedelta(0)) as session:
        await session.request("POST", EXPORT, json=[1, 2])

    assert seen[0].headers["content-type"] == "application/json"
    assert seen[0].content == b"[1, 2]"


async def test_each_session_has_its_own_cookies_and_the_shared_client_none() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=BODY, headers={"set-cookie": "sid=s1; Path=/"})

    fetcher, created = _session_fetcher(handler)
    async with fetcher.session(crawl_delay=timedelta(0)) as session:
        await session.request("GET", PAGE)
    async with fetcher.session(crawl_delay=timedelta(0)) as session:
        await session.request("GET", PAGE)
    await fetcher.fetch(URL, previous=None, change_signal=ChangeSignal.CONTENT_HASH)

    assert [request.headers.get("cookie") for request in seen] == [None, None, None]
    assert len(created) == 2
    assert all(client.is_closed for client in created)


async def test_the_crawl_delay_is_waited_before_every_session_request() -> None:
    sleeps: list[float] = []
    fetcher, _ = _session_fetcher(lambda request: httpx2.Response(200, content=b"x"), sleeps=sleeps)

    async with fetcher.session(crawl_delay=timedelta(seconds=10)) as session:
        await session.request("GET", PAGE)
        await session.request("POST", EXPORT, json={})
        await session.request("GET", EXPORT)

    assert sleeps == [10.0, 10.0, 10.0]


async def test_a_post_is_never_repeated() -> None:
    attempts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request.method)
        raise httpx2.ConnectError("refused", request=request)

    fetcher, _ = _session_fetcher(handler)
    async with fetcher.session(crawl_delay=timedelta(0)) as session:
        posted = await session.request("POST", EXPORT, json={})
        got = await session.request("GET", EXPORT)

    assert attempts == ["POST", "GET", "GET"]
    assert isinstance(posted, FetchFailure)
    assert posted.outcome is Outcome.SOURCE_UNAVAILABLE
    assert posted.note == "ConnectError (not repeated)"
    assert posted.fetched_at == NOW
    assert isinstance(got, FetchFailure)
    assert got.note == f"ConnectError after {RETRY_ATTEMPTS + 1} attempts"


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (httpx2.Response(403), Outcome.BLOCKED),
        (httpx2.Response(429, headers={"retry-after": "60"}), Outcome.BLOCKED),
        (httpx2.Response(500), Outcome.SOURCE_UNAVAILABLE),
        (httpx2.Response(404), Outcome.FAILED),
        (
            httpx2.Response(
                200,
                content=b"<div class='cf-turnstile'></div>",
                headers={"content-type": "text/html"},
            ),
            Outcome.BLOCKED,
        ),
    ],
)
async def test_session_answers_are_classified_like_fetch(
    answer: httpx2.Response, outcome: Outcome
) -> None:
    fetcher, _ = _session_fetcher(lambda request: answer)

    async with fetcher.session(crawl_delay=timedelta(0)) as session:
        result = await session.request("POST", EXPORT, json={})

    assert isinstance(result, FetchFailure)
    assert result.outcome is outcome
    assert result.status == answer.status_code
    assert result.fetched_at == NOW


async def test_a_session_body_over_the_cap_fails() -> None:
    created: list[httpx2.AsyncClient] = []

    def session_client() -> httpx2.AsyncClient:
        transport = httpx2.MockTransport(lambda request: httpx2.Response(200, content=BODY))
        created.append(httpx2.AsyncClient(transport=transport))
        return created[-1]

    async with httpx2.AsyncClient() as shared:
        fetcher = HttpFetcher(
            shared, FixedClock(NOW), session_client=session_client, max_body_bytes=10
        )
        async with fetcher.session(crawl_delay=timedelta(0)) as session:
            result = await session.request("GET", EXPORT)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert "exceeds the cap" in result.note


async def test_sessions_need_a_session_client() -> None:
    async with httpx2.AsyncClient() as client:
        fetcher = HttpFetcher(client, FixedClock(NOW))

        with pytest.raises(TypeError, match="session_client"):
            async with fetcher.session(crawl_delay=timedelta(0)):
                pass


def test_document_from_hashes_and_sizes_the_body() -> None:
    response = Response(
        status=200,
        body=BODY,
        etag=None,
        last_modified=None,
        content_type="text/csv",
        final_url=URL,
    )

    document = document_from(response, NOW)

    assert document.body == BODY
    assert document.byte_size == len(BODY)
    assert len(document.content_hash) == 64
    assert document.fetched_at == NOW
    assert document.final_url == URL
    assert document.status == 200
