"""The headed-browser fallback: the challenge predicate, the outcome mapping over a scripted
Playwright double, and (``live``) a real headed Chrome against a local server."""

import base64
import hashlib
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Playwright

from credentialing.connectors.framework.browser import ChromeFetcher, is_challenge
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, NotModified
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import SourceSnapshot
from tests.fakes import FixedClock
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

_URL = "https://example.test/medicaid/"
_DOCUMENT = (
    b"<html><head><title>Medicaid</title></head><body>"
    b'<a href="/uploads/list.pdf">List</a>'
    b"<script>var u='/cdn-cgi/challenge-platform/scripts/jsd/main.js'</script>"
    b"</body></html>"
)
_CHALLENGE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body><script>window._cf_chl_opt={}</script></body></html>"
)


@pytest.mark.parametrize(
    ("status", "url", "html"),
    [
        (403, _URL, "<html><title>Medicaid</title></html>"),
        (503, _URL, "<html><title>Medicaid</title></html>"),
        (200, _URL, "<html><head><title>Just a moment...</title></head></html>"),
        (200, _URL, "<title>Attention Required! | Cloudflare</title>"),
        (200, _URL, "<title>Checking your browser before accessing</title>"),
        (200, _URL, "<title>hCaptcha check</title>"),
        (200, _URL, "<title>Verifying you are human</title>"),
        (200, _URL, '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/x"></script>'),
        (200, _URL, "<script>window._cf_chl_opt={cvId: '3'}</script>"),
        (200, _URL, '<div id="cf-challenge-running"></div>'),
        (200, _URL, '<div class="cf-turnstile" data-sitekey="x"></div>'),
        (200, _URL, '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js">'),
        (200, "https://validate.perfdrive.com/?ssa=1", "<html><title>Medicaid</title></html>"),
    ],
    ids=[
        "403",
        "503",
        "just-a-moment",
        "attention-required",
        "checking-browser",
        "captcha-title",
        "verifying",
        "challenge-script",
        "challenge-options",
        "cf-challenge",
        "turnstile",
        "turnstile-script",
        "perfdrive-host",
    ],
)
def test_a_challenge_page_is_recognised(status: int, url: str, html: str) -> None:
    assert is_challenge(status=status, url=url, html=html)


def test_an_ordinary_page_with_cloudflare_detection_script_is_not_a_challenge() -> None:
    html = _DOCUMENT.decode() + "<p>This form is protected by reCAPTCHA</p>"

    assert not is_challenge(status=200, url=_URL, html=html)


class _Request:
    def is_navigation_request(self) -> bool:
        return True


class _Response:
    def __init__(self, status: int, frame: object) -> None:
        self.status = status
        self.frame = frame
        self.request = _Request()


type _State = tuple[int, str | Exception]


class _Page:
    """Shows ``states`` one after another, one per look at the page, then stays on the last."""

    def __init__(self, states: Sequence[_State], answer: dict[str, object] | Exception) -> None:
        self.main_frame = object()
        self.url = "about:blank"
        self._states = list(states)
        self._answer = answer
        self._looks = 0
        self._handlers: list[Callable[[_Response], None]] = []
        self.evaluated: list[object] = []

    def set_default_timeout(self, timeout: float) -> None:
        pass

    def on(self, event: str, handler: Callable[[_Response], None]) -> None:
        self._handlers.append(handler)

    def _show(self, index: int) -> None:
        for handler in self._handlers:
            handler(_Response(self._states[index][0], self.main_frame))

    async def goto(self, url: str, **_: object) -> None:
        self.url = url
        self._show(0)

    async def content(self) -> str:
        index = min(self._looks, len(self._states) - 1)
        if 0 < self._looks < len(self._states):
            self._show(index)
        self._looks += 1
        html = self._states[index][1]
        if isinstance(html, Exception):
            raise html
        return html

    async def evaluate(self, script: str, arg: object) -> object:
        self.evaluated.append(arg)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


class _Context:
    def __init__(self, page: _Page) -> None:
        self.pages = [page]
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Chromium:
    def __init__(self, context: _Context | Exception) -> None:
        self._context = context
        self.launched: list[dict[str, object]] = []

    async def launch_persistent_context(self, **options: object) -> _Context:
        self.launched.append(options)
        if isinstance(self._context, Exception):
            raise self._context
        return self._context


class _Playwright:
    def __init__(self, chromium: _Chromium) -> None:
        self.chromium = chromium


class _Started:
    def __init__(self, playwright: _Playwright | Exception) -> None:
        self._playwright = playwright
        self.stopped = False

    async def __aenter__(self) -> Playwright:
        if isinstance(self._playwright, Exception):
            raise self._playwright
        return cast(Playwright, self._playwright)

    async def __aexit__(self, *_: object) -> None:
        self.stopped = True


class _Browser:
    """A scripted Playwright: one page showing ``states``, answering the in-page fetch."""

    def __init__(
        self,
        states: Sequence[_State] = ((200, _DOCUMENT.decode()),),
        answer: dict[str, object] | Exception | None = None,
        *,
        launch: Exception | None = None,
        start: Exception | None = None,
    ) -> None:
        self.page = _Page(states, _answer() if answer is None else answer)
        self.context = _Context(self.page)
        self.chromium = _Chromium(launch if launch is not None else self.context)
        self.started = _Started(start if start is not None else _Playwright(self.chromium))
        self.sleeps: list[float] = []

    def factory(self) -> AbstractAsyncContextManager[Playwright]:
        return self.started

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def fetcher(self, challenge_timeout: timedelta) -> ChromeFetcher:
        return ChromeFetcher(
            FixedClock(NOW),
            profile_dir=Path("profile"),
            challenge_timeout=challenge_timeout,
            sleep=self.sleep,
            playwright=self.factory,
        )


def _answer(
    body: bytes = _DOCUMENT, *, status: int = 200, content_type: str = "text/html; charset=UTF-8"
) -> dict[str, object]:
    return {
        "status": status,
        "content_type": content_type,
        "url": _URL,
        "size": len(body),
        "body": base64.b64encode(body).decode(),
    }


async def _fetch(
    browser: _Browser,
    *,
    previous: SourceSnapshot | None = None,
    crawl_delay: timedelta = timedelta(0),
    challenge_timeout: timedelta = timedelta(seconds=45),
) -> object:
    return await browser.fetcher(challenge_timeout).fetch(
        _URL, previous=previous, change_signal=ChangeSignal.CONTENT_HASH, crawl_delay=crawl_delay
    )


async def test_a_challenge_that_clears_by_itself_yields_the_document_read_in_the_page() -> None:
    browser = _Browser([(403, _CHALLENGE), (403, _CHALLENGE), (200, _DOCUMENT.decode())])

    result = await _fetch(browser, crawl_delay=timedelta(seconds=10))

    assert isinstance(result, FetchedDocument)
    assert result.fetched_in_browser is True
    assert result.body == _DOCUMENT
    assert result.content_hash == hashlib.sha256(_DOCUMENT).hexdigest()
    assert (result.status, result.content_type, result.final_url) == (
        200,
        "text/html; charset=UTF-8",
        _URL,
    )
    assert (result.etag, result.last_modified, result.fetched_at) == (None, None, NOW)
    assert browser.sleeps == [10.0, 0.5, 0.5]
    assert browser.page.evaluated == [{"url": _URL, "max": 50 * 1024 * 1024}]
    assert browser.context.closed
    assert browser.started.stopped


async def test_chrome_is_launched_headed_with_its_own_identity_and_no_downloads() -> None:
    browser = _Browser()

    await _fetch(browser)

    assert browser.chromium.launched == [
        {
            "user_data_dir": Path("profile"),
            "channel": "chrome",
            "headless": False,
            "accept_downloads": False,
            "timeout": 60_000.0,
        }
    ]


async def test_a_page_navigating_away_from_its_challenge_is_looked_at_again() -> None:
    gone = PlaywrightError("Execution context was destroyed")
    browser = _Browser([(403, _CHALLENGE), (200, gone), (200, _DOCUMENT.decode())])

    result = await _fetch(browser)

    assert isinstance(result, FetchedDocument)
    assert browser.sleeps == [0.5, 0.5]


async def test_a_challenge_that_does_not_clear_is_blocked_and_nothing_is_read() -> None:
    browser = _Browser([(403, _CHALLENGE)])

    result = await _fetch(browser, challenge_timeout=timedelta(seconds=1))

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (Outcome.BLOCKED, 403)
    assert result.note == "challenge page not cleared (HTTP 403)"
    assert browser.sleeps == [0.5, 0.5]
    assert browser.page.evaluated == []
    assert browser.context.closed


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (403, Outcome.BLOCKED),
        (429, Outcome.BLOCKED),
        (503, Outcome.BLOCKED),
        (500, Outcome.SOURCE_UNAVAILABLE),
        (404, Outcome.FAILED),
    ],
)
async def test_an_error_status_in_the_page_is_its_outcome(status: int, outcome: Outcome) -> None:
    result = await _fetch(_Browser(answer=_answer(b"no", status=status)))

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (outcome, status)


async def test_a_challenge_answering_the_in_page_request_is_blocked() -> None:
    result = await _fetch(_Browser(answer=_answer(_CHALLENGE.encode())))

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.BLOCKED
    assert result.note == "challenge page (HTTP 200) in the browser"


async def test_a_body_over_the_cap_is_failed() -> None:
    answer = {**_answer(), "size": 60 * 1024 * 1024, "body": None}

    result = await _fetch(_Browser(answer=answer))

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert result.note == "body of 62914560 bytes exceeds the cap in the browser"


async def test_the_same_content_as_the_previous_fetch_is_unchanged() -> None:
    previous = SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url=_URL,
        content_hash=hashlib.sha256(b"%PDF-1.4").hexdigest(),
    )
    answer = _answer(b"%PDF-1.4", content_type="application/pdf")

    result = await _fetch(_Browser(answer=answer), previous=previous)

    assert isinstance(result, NotModified)
    assert result.reason == "content hash unchanged"
    assert result.fetched_in_browser is True


@pytest.mark.parametrize(
    "browser",
    [
        pytest.param(_Browser(start=PlaywrightError("driver missing")), id="driver"),
        pytest.param(_Browser(launch=PlaywrightError("no 'chrome' channel")), id="chrome"),
        pytest.param(_Browser(launch=FileNotFoundError("profile")), id="profile"),
    ],
)
async def test_a_browser_that_cannot_start_is_failed_never_raised(browser: _Browser) -> None:
    result = await _fetch(browser)

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (Outcome.FAILED, None)
    assert result.note.startswith("browser unavailable: ")


@pytest.mark.parametrize(
    "answer",
    [PlaywrightError("Target closed"), {"status": "x"}],
    ids=["playwright-error", "unexpected-answer"],
)
async def test_a_browser_failing_mid_fetch_is_failed_and_closed(
    answer: dict[str, object] | Exception,
) -> None:
    browser = _Browser(answer=answer)

    result = await _fetch(browser)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert result.note.startswith("browser fetch failed: ")
    assert browser.context.closed


# --- live: a real headed Chrome against a local server ------------------------------------

_LIVE_PAGE = b"<html><head><title>Made-up list</title></head><body>Avery Testperson</body></html>"
_LIVE_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
_LIVE_CHALLENGE = (
    b"<html><head><title>Just a moment...</title></head><body>"
    b"<script>document.cookie='cleared=1; path=/';setTimeout(()=>location.reload(),1000)</script>"
    b"</body></html>"
)
_LIVE_TURNSTILE = (
    b'<html><head><title>Medicaid</title></head><body><div class="cf-turnstile"></div>'
    b"</body></html>"
)


class _LocalSite(BaseHTTPRequestHandler):
    """``/`` is a challenge that clears by itself (a cookie and a reload), ``/list.pdf`` a
    file behind it, ``/checkbox`` an interactive challenge that never clears."""

    def do_GET(self) -> None:
        cleared = "cleared=1" in (self.headers.get("Cookie") or "")
        if self.path == "/checkbox":
            self._send(403, "text/html", _LIVE_TURNSTILE)
        elif not cleared:
            self._send(403, "text/html", _LIVE_CHALLENGE)
        elif self.path == "/list.pdf":
            self._send(200, "application/pdf", _LIVE_PDF)
        else:
            self._send(200, "text/html; charset=utf-8", _LIVE_PAGE)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def local_site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalSite)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.live
async def test_headed_chrome_waits_out_a_challenge_and_reads_page_and_file(
    local_site: str, tmp_path: Path
) -> None:
    fetcher = ChromeFetcher(FixedClock(NOW), profile_dir=tmp_path / "profile")

    page = await fetcher.fetch(
        f"{local_site}/", previous=None, change_signal=ChangeSignal.CONTENT_HASH
    )
    file = await fetcher.fetch(
        f"{local_site}/list.pdf", previous=None, change_signal=ChangeSignal.CONTENT_HASH
    )

    assert isinstance(page, FetchedDocument), page
    assert (page.body, page.status, page.fetched_in_browser) == (_LIVE_PAGE, 200, True)
    assert isinstance(file, FetchedDocument), file
    assert (file.body, file.content_type) == (_LIVE_PDF, "application/pdf")


@pytest.mark.live
async def test_headed_chrome_never_clicks_an_interactive_challenge(
    local_site: str, tmp_path: Path
) -> None:
    fetcher = ChromeFetcher(
        FixedClock(NOW), profile_dir=tmp_path / "profile", challenge_timeout=timedelta(seconds=2)
    )

    result = await fetcher.fetch(
        f"{local_site}/checkbox", previous=None, change_signal=ChangeSignal.CONTENT_HASH
    )

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (Outcome.BLOCKED, 403)
