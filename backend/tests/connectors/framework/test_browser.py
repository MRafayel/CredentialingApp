"""The headed-browser fallback: the challenge predicate, the outcome mapping over a scripted
Playwright double (pages, inline documents and downloads), and (``live``) a real headed Chrome
against a local server."""

import base64
import hashlib
import logging
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

from credentialing.connectors.framework.browser import PROFILE_KEEP, ChromeFetcher, is_challenge
from credentialing.connectors.framework.http import FetchedDocument, FetchFailure, NotModified
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import SourceSnapshot
from tests.fakes import FixedClock
from tests.synthetic import NOW

pytestmark = pytest.mark.anyio

_URL = "https://example.test/medicaid/"
_FILE_URL = "https://example.test/main/idcplg?IdcService=GET_FILE&dDocName=LIST"
_WORKBOOK = b"PK\x03\x04 made-up workbook bytes"
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
_RADWARE = (
    "<html><head><title>Radware Page</title></head>"
    "<body><p>Verifying your browser before proceeding...</p></body></html>"
)
_DOWNLOAD_STARTING = PlaywrightError("Page.goto: Download is starting")


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
        (200, _URL, "<html><head><title>Radware Page</title></head></html>"),
        (200, _URL, "<html><head><title>Radware Captcha Page</title></head></html>"),
        (200, _URL, "<body><p>Verifying your browser before proceeding...</p></body>"),
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
        "radware-interstitial",
        "radware-captcha",
        "radware-sentence",
    ],
)
def test_a_challenge_page_is_recognised(status: int, url: str, html: str) -> None:
    assert is_challenge(status=status, url=url, html=html)


def test_an_ordinary_page_with_cloudflare_detection_script_is_not_a_challenge() -> None:
    html = _DOCUMENT.decode() + "<p>This form is protected by reCAPTCHA</p>"

    assert not is_challenge(status=200, url=_URL, html=html)


def test_an_ordinary_title_is_not_a_challenge() -> None:
    html = "<html><head><title>Excluded Provider Lists - Minnesota DHS</title></head></html>"

    assert not is_challenge(status=200, url=_URL, html=html)


class _Request:
    def is_navigation_request(self) -> bool:
        return True


class _Response:
    def __init__(self, status: int, frame: object) -> None:
        self.status = status
        self.frame = frame
        self.request = _Request()


class _Download:
    """A download that finished (or failed) into ``folder``; deleting it removes the file."""

    def __init__(
        self,
        body: bytes,
        folder: Path,
        *,
        url: str = _FILE_URL,
        failure: str | None = None,
    ) -> None:
        self.url = url
        self.suggested_filename = "Made-up list 10-01-26.xlsx"
        self._failure = failure
        self._path = folder / f"{uuid4()}.download"
        self._path.write_bytes(body)
        self.deleted = False

    async def failure(self) -> str | None:
        return self._failure

    async def path(self) -> Path:
        return self._path

    async def delete(self) -> None:
        self._path.unlink(missing_ok=True)
        self.deleted = True


type _State = tuple[int, str | Exception | _Download]


class _Page:
    """Shows ``states`` one after another, one per look at the page, then stays on the last.

    A ``_Download`` state emits the download while the page keeps showing the state before it
    (a challenge that reloads into a file). ``goto_error`` makes the navigation (only to
    ``goto_error_url`` when given) raise after emitting ``goto_download``; ``late_download``
    arrives at the first pause. ``routes`` gives a URL its own states, shown from its first
    look when it is navigated to; ``gotos`` records every navigation and ``referers`` its
    Referer. ``load_error`` makes waiting for the ``load`` event raise.
    """

    def __init__(
        self,
        states: Sequence[_State],
        answer: dict[str, object] | Exception,
        *,
        goto_error: Exception | None = None,
        goto_error_url: str | None = None,
        goto_download: _Download | None = None,
        late_download: _Download | None = None,
        routes: dict[str, Sequence[_State]] | None = None,
        load_error: Exception | None = None,
    ) -> None:
        self.main_frame = object()
        self.url = "about:blank"
        self._states = list(states)
        self._answer = answer
        self._goto_error = goto_error
        self._goto_error_url = goto_error_url
        self._goto_download = goto_download
        self._late_download = late_download
        self._routes = routes or {}
        self.gotos: list[str] = []
        self.referers: list[str | None] = []
        self.load_waits: list[str] = []
        self._load_error = load_error
        self._looks = 0
        self._handlers: dict[str, list[Callable[[object], None]]] = {}
        self.evaluated: list[object] = []
        self.content_reads = 0

    def set_default_timeout(self, timeout: float) -> None:
        pass

    def on(self, event: str, handler: Callable[[object], None]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def _emit(self, event: str, value: object) -> None:
        for handler in self._handlers.get(event, []):
            handler(value)

    def _show(self, index: int) -> None:
        state = self._states[index]
        if isinstance(state[1], _Download):
            self._emit("download", state[1])
        else:
            self._emit("response", _Response(state[0], self.main_frame))

    def tick(self) -> None:
        if self._late_download is not None:
            self._emit("download", self._late_download)
            self._late_download = None

    async def goto(self, url: str, *, referer: str | None = None, **_: object) -> None:
        self.gotos.append(url)
        self.referers.append(referer)
        if self._goto_error is not None and self._goto_error_url in (None, url):
            if self._goto_download is not None:
                self._emit("download", self._goto_download)
            raise self._goto_error
        if url in self._routes:
            self._states = list(self._routes[url])
            self._looks = 0
        self.url = url
        self._show(0)

    async def wait_for_load_state(self, state: str, **_: object) -> None:
        self.load_waits.append(state)
        if self._load_error is not None:
            raise self._load_error

    async def content(self) -> str:
        self.content_reads += 1
        index = min(self._looks, len(self._states) - 1)
        if 0 < self._looks < len(self._states):
            self._show(index)
        self._looks += 1
        html = self._states[index][1]
        while isinstance(html, _Download):
            index -= 1
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
    """Answers cookie reads: a detection cookie at once, the clearance cookie from read number
    ``clearance_after`` on (never when ``None``)."""

    def __init__(self, page: _Page, *, clearance_after: int | None = 0) -> None:
        self.pages = [page]
        self.closed = False
        self._clearance_after = clearance_after
        self.cookie_reads: list[list[str]] = []

    async def cookies(self, urls: list[str]) -> list[dict[str, str]]:
        self.cookie_reads.append(urls)
        jar = [{"name": "__cf_bm", "value": "made-up"}]
        if self._clearance_after is not None and len(self.cookie_reads) > self._clearance_after:
            jar.append({"name": "cf_clearance", "value": "made-up"})
        return jar

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
        goto_error: Exception | None = None,
        goto_error_url: str | None = None,
        goto_download: _Download | None = None,
        late_download: _Download | None = None,
        routes: dict[str, Sequence[_State]] | None = None,
        load_error: Exception | None = None,
        clearance_after: int | None = 0,
        max_body_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.page = _Page(
            states,
            _answer() if answer is None else answer,
            goto_error=goto_error,
            goto_error_url=goto_error_url,
            goto_download=goto_download,
            late_download=late_download,
            routes=routes,
            load_error=load_error,
        )
        self._max_body_bytes = max_body_bytes
        self.context = _Context(self.page, clearance_after=clearance_after)
        self.chromium = _Chromium(launch if launch is not None else self.context)
        self.started = _Started(start if start is not None else _Playwright(self.chromium))
        self.sleeps: list[float] = []

    def factory(self) -> AbstractAsyncContextManager[Playwright]:
        return self.started

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.page.tick()

    def fetcher(
        self,
        challenge_timeout: timedelta,
        warm_up_timeout: timedelta = timedelta(seconds=30),
        profile_dir: Path = Path("profile"),
    ) -> ChromeFetcher:
        return ChromeFetcher(
            FixedClock(NOW),
            profile_dir=profile_dir,
            challenge_timeout=challenge_timeout,
            warm_up_timeout=warm_up_timeout,
            max_body_bytes=self._max_body_bytes,
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
    warm_up_timeout: timedelta = timedelta(seconds=30),
    entry_url: str | None = None,
    profile_dir: Path = Path("profile"),
    profile: str | None = None,
    webdriver_disabled: bool = False,
    fetch_challenge_timeout: timedelta | None = None,
) -> object:
    return await browser.fetcher(challenge_timeout, warm_up_timeout, profile_dir).fetch(
        _URL,
        previous=previous,
        change_signal=ChangeSignal.CONTENT_HASH,
        crawl_delay=crawl_delay,
        entry_url=entry_url,
        profile=profile,
        webdriver_disabled=webdriver_disabled,
        challenge_timeout=fetch_challenge_timeout,
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


async def test_chrome_is_launched_headed_with_its_own_identity_and_downloads() -> None:
    browser = _Browser()

    await _fetch(browser)

    assert browser.chromium.launched == [
        {
            "user_data_dir": Path("profile"),
            "channel": "chrome",
            "headless": False,
            "accept_downloads": True,
            "downloads_path": Path("profile/downloads"),
            "timeout": 60_000.0,
        }
    ]


async def test_a_source_profile_is_its_own_folder_and_hiding_the_marker_adds_one_argument(
    caplog: pytest.LogCaptureFixture,
) -> None:
    browser = _Browser()

    with caplog.at_level(logging.INFO):
        await _fetch(browser, profile="ak_dhss", webdriver_disabled=True)

    assert browser.chromium.launched == [
        {
            "user_data_dir": Path("profile/ak_dhss"),
            "channel": "chrome",
            "headless": False,
            "accept_downloads": True,
            "downloads_path": Path("profile/ak_dhss/downloads"),
            "timeout": 60_000.0,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
    ]
    assert "profile=ak_dhss webdriver_disabled=True" in caplog.text


async def test_a_source_profile_keeps_chrome_marker_unless_asked() -> None:
    browser = _Browser()

    await _fetch(browser, profile="wy_wdh")

    [launched] = browser.chromium.launched
    assert launched["user_data_dir"] == Path("profile/wy_wdh")
    assert "args" not in launched


@pytest.mark.parametrize("profile", ["", "../outside", "Ak_Dhss", "ak/dhss", "ak dhss"])
async def test_a_profile_that_is_not_a_plain_name_is_a_programming_error(profile: str) -> None:
    browser = _Browser()

    with pytest.raises(ValueError, match="not a profile name"):
        await _fetch(browser, profile=profile)
    assert browser.chromium.launched == []


async def test_a_fetch_challenge_timeout_replaces_the_fetcher_default_for_that_fetch() -> None:
    browser = _Browser([(403, _CHALLENGE)])
    fetcher = browser.fetcher(timedelta(seconds=1))

    longer = await fetcher.fetch(
        _URL,
        previous=None,
        change_signal=ChangeSignal.CONTENT_HASH,
        challenge_timeout=timedelta(seconds=2),
    )
    waited = len(browser.sleeps)
    default = await fetcher.fetch(_URL, previous=None, change_signal=ChangeSignal.CONTENT_HASH)

    assert isinstance(longer, FetchFailure)
    assert longer.outcome is Outcome.BLOCKED
    assert isinstance(default, FetchFailure)
    assert default.outcome is Outcome.BLOCKED
    assert waited == 4
    assert len(browser.sleeps) - waited == 2


async def test_a_fetch_challenge_timeout_applies_to_the_entry_page_too() -> None:
    browser = _Browser(routes={_ENTRY_URL: [(403, _CHALLENGE)]})

    result = await _fetch(
        browser,
        entry_url=_ENTRY_URL,
        challenge_timeout=timedelta(seconds=1),
        fetch_challenge_timeout=timedelta(seconds=3),
    )

    assert isinstance(result, FetchFailure)
    assert result.note == "challenge page not cleared on the entry page (HTTP 403)"
    assert browser.sleeps == [0.5] * 6


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


def _files_in(folder: Path) -> list[Path]:
    return list(folder.iterdir())


def _previous(content_hash: str) -> SourceSnapshot:
    return SourceSnapshot(
        id=uuid4(),
        source_id="test_exclusions",
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url=_FILE_URL,
        content_hash=content_hash,
    )


async def test_a_navigation_that_becomes_a_download_yields_the_file(tmp_path: Path) -> None:
    download = _Download(_WORKBOOK, tmp_path)
    browser = _Browser(goto_error=_DOWNLOAD_STARTING, goto_download=download)

    result = await _fetch(browser)

    assert isinstance(result, FetchedDocument)
    assert result.fetched_in_browser is True
    assert result.body == _WORKBOOK
    assert result.content_hash == hashlib.sha256(_WORKBOOK).hexdigest()
    assert (result.status, result.content_type, result.final_url) == (200, None, _FILE_URL)
    assert (result.byte_size, result.fetched_at) == (len(_WORKBOOK), NOW)
    assert browser.page.evaluated == []
    assert browser.page.content_reads == 0
    assert download.deleted
    assert _files_in(tmp_path) == []
    assert browser.context.closed


async def test_a_challenge_that_reloads_into_a_download_yields_the_file(tmp_path: Path) -> None:
    download = _Download(_WORKBOOK, tmp_path)
    browser = _Browser([(200, _RADWARE), (200, _RADWARE), (200, download)])

    result = await _fetch(browser, crawl_delay=timedelta(seconds=5))

    assert isinstance(result, FetchedDocument)
    assert result.body == _WORKBOOK
    assert browser.sleeps == [5.0, 0.5, 0.5, 0.5]
    assert browser.page.evaluated == []
    assert download.deleted


async def test_a_download_arriving_after_the_aborted_navigation_is_waited_for(
    tmp_path: Path,
) -> None:
    download = _Download(_WORKBOOK, tmp_path)
    browser = _Browser(goto_error=_DOWNLOAD_STARTING, late_download=download)

    result = await _fetch(browser)

    assert isinstance(result, FetchedDocument)
    assert result.body == _WORKBOOK
    assert browser.sleeps == [0.5]


async def test_an_aborted_navigation_without_a_download_is_failed_not_read_in_the_page() -> None:
    browser = _Browser(goto_error=_DOWNLOAD_STARTING)

    result = await _fetch(browser, challenge_timeout=timedelta(seconds=1))

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (Outcome.FAILED, None)
    assert result.note == "download did not arrive in the browser"
    assert browser.sleeps == [0.5, 0.5]
    assert browser.page.evaluated == []
    assert browser.context.closed


async def test_another_navigation_error_is_failed_at_once() -> None:
    browser = _Browser(goto_error=PlaywrightError("net::ERR_NAME_NOT_RESOLVED"))

    result = await _fetch(browser)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert result.note == "browser fetch failed: Error"
    assert browser.sleeps == []
    assert browser.context.closed


async def test_a_downloaded_file_with_the_previous_hash_is_unchanged(tmp_path: Path) -> None:
    download = _Download(_WORKBOOK, tmp_path)
    browser = _Browser(goto_error=_DOWNLOAD_STARTING, goto_download=download)

    result = await _fetch(browser, previous=_previous(hashlib.sha256(_WORKBOOK).hexdigest()))

    assert isinstance(result, NotModified)
    assert (result.reason, result.fetched_in_browser) == ("content hash unchanged", True)
    assert download.deleted


async def test_a_downloaded_file_over_the_cap_is_failed_and_deleted(tmp_path: Path) -> None:
    download = _Download(_WORKBOOK, tmp_path)
    browser = _Browser(
        goto_error=_DOWNLOAD_STARTING, goto_download=download, max_body_bytes=len(_WORKBOOK) - 1
    )

    result = await _fetch(browser)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.FAILED
    assert result.note == f"body of {len(_WORKBOOK)} bytes exceeds the cap in the browser"
    assert download.deleted


async def test_a_failed_download_is_failed_without_its_failure_text(tmp_path: Path) -> None:
    download = _Download(b"", tmp_path, failure="canceled")
    browser = _Browser(goto_error=_DOWNLOAD_STARTING, goto_download=download)

    result = await _fetch(browser)

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (Outcome.FAILED, None)
    assert result.note == "download failed in the browser"
    assert download.deleted


@pytest.mark.parametrize(
    "body",
    [
        _RADWARE.encode(),
        b"\n  " + _CHALLENGE.encode(),
        b"\xef\xbb\xbf<!DOCTYPE html>" + _RADWARE.encode(),
    ],
    ids=["radware", "leading-whitespace", "bom-doctype"],
)
async def test_a_challenge_page_delivered_as_a_download_is_blocked(
    body: bytes, tmp_path: Path
) -> None:
    download = _Download(body, tmp_path)
    browser = _Browser(goto_error=_DOWNLOAD_STARTING, goto_download=download)

    result = await _fetch(browser)

    assert isinstance(result, FetchFailure)
    assert result.outcome is Outcome.BLOCKED
    assert result.note == "challenge page delivered as a download"


async def test_an_ordinary_html_download_passes_through_for_the_parser(tmp_path: Path) -> None:
    download = _Download(_DOCUMENT, tmp_path)
    browser = _Browser(goto_error=_DOWNLOAD_STARTING, goto_download=download)

    result = await _fetch(browser)

    assert isinstance(result, FetchedDocument)
    assert result.body == _DOCUMENT


_ENTRY_URL = "https://example.test/medicaid-program-integrity"


async def test_the_entry_page_clears_and_sets_its_cookie_then_the_file_downloads(
    tmp_path: Path,
) -> None:
    download = _Download(_WORKBOOK, tmp_path)
    browser = _Browser(
        routes={_ENTRY_URL: [(403, _CHALLENGE), (200, _DOCUMENT.decode())]},
        goto_error=_DOWNLOAD_STARTING,
        goto_error_url=_URL,
        goto_download=download,
        clearance_after=3,
    )

    result = await _fetch(browser, crawl_delay=timedelta(seconds=10), entry_url=_ENTRY_URL)

    assert isinstance(result, FetchedDocument)
    assert result.body == _WORKBOOK
    assert browser.page.gotos == [_ENTRY_URL, _URL]
    assert browser.page.load_waits == ["load"]
    assert browser.context.cookie_reads == [[_ENTRY_URL]] * 4
    # crawl delay, challenge poll, three cookie polls, the dwell
    assert browser.sleeps == [10.0, 0.5, 0.5, 0.5, 0.5, 10.0]
    assert browser.page.referers == [None, _ENTRY_URL]
    assert browser.page.evaluated == []
    assert download.deleted


async def test_the_page_after_the_entry_page_is_read_at_its_own_url() -> None:
    browser = _Browser(routes={_ENTRY_URL: [(200, _DOCUMENT.decode())]})

    result = await _fetch(browser, entry_url=_ENTRY_URL)

    assert isinstance(result, FetchedDocument)
    assert browser.page.gotos == [_ENTRY_URL, _URL]
    assert browser.page.evaluated == [{"url": _URL, "max": 50 * 1024 * 1024}]


async def test_an_entry_page_that_stays_a_challenge_is_blocked_before_the_file() -> None:
    browser = _Browser(routes={_ENTRY_URL: [(403, _CHALLENGE)]})

    result = await _fetch(browser, entry_url=_ENTRY_URL, challenge_timeout=timedelta(seconds=1))

    assert isinstance(result, FetchFailure)
    assert (result.outcome, result.status) == (Outcome.BLOCKED, 403)
    assert result.note == "challenge page not cleared on the entry page (HTTP 403)"
    assert browser.page.gotos == [_ENTRY_URL]
    assert browser.context.cookie_reads == []
    assert browser.sleeps == [0.5, 0.5]
    assert browser.page.evaluated == []
    assert browser.context.closed


async def test_an_entry_page_equal_to_the_url_is_not_opened_twice() -> None:
    browser = _Browser()

    result = await _fetch(browser, entry_url=_URL)

    assert isinstance(result, FetchedDocument)
    assert browser.page.gotos == [_URL]
    assert browser.page.referers == [None]
    assert browser.context.cookie_reads == []
    assert browser.sleeps == []


async def test_without_a_clearance_cookie_the_warm_up_ends_at_its_timeout_and_goes_on() -> None:
    browser = _Browser(routes={_ENTRY_URL: [(200, _DOCUMENT.decode())]}, clearance_after=None)

    result = await _fetch(browser, entry_url=_ENTRY_URL, warm_up_timeout=timedelta(seconds=1))

    assert isinstance(result, FetchedDocument)
    assert browser.page.gotos == [_ENTRY_URL, _URL]
    assert len(browser.context.cookie_reads) == 3
    assert browser.sleeps == [0.5, 0.5, 10.0]
    assert browser.page.referers == [None, _ENTRY_URL]


async def test_an_entry_page_that_never_finishes_loading_is_waited_on_no_longer() -> None:
    browser = _Browser(
        routes={_ENTRY_URL: [(200, _DOCUMENT.decode())]},
        load_error=PlaywrightError("Timeout 60000ms exceeded."),
    )

    result = await _fetch(browser, entry_url=_ENTRY_URL)

    assert isinstance(result, FetchedDocument)
    assert browser.page.load_waits == ["load"]
    assert browser.sleeps == [10.0]
    assert browser.page.gotos == [_ENTRY_URL, _URL]


async def test_without_a_warm_up_there_is_no_referer_and_no_dwell() -> None:
    browser = _Browser()

    result = await _fetch(browser)

    assert isinstance(result, FetchedDocument)
    assert browser.page.referers == [None]
    assert browser.page.load_waits == []
    assert browser.context.cookie_reads == []


# --- the profile: trimmed to its allow-list after every fetch ------------------------------

_CLUTTER = (
    "Default/Cache/Cache_Data/f_000001",
    "Default/Code Cache/js/index",
    "Default/History",
    "Default/Favicons",
    "Default/Sessions/Session_1",
    "GPUCache/data_0",
    "Safe Browsing/UrlSoceng.store",
    "Variations",
    "downloads/0f3c.download",
)


def _profile(root: Path) -> Path:
    """A made-up closed profile: every kept path (``Local Storage`` with nested files) and
    clutter at the top level, inside ``Default`` and in ``downloads``."""
    profile = root / "profile"
    for kept in PROFILE_KEEP - {"Default/Local Storage"}:
        (profile / kept).parent.mkdir(parents=True, exist_ok=True)
        (profile / kept).write_bytes(b"made-up")
    (profile / "Default/Local Storage/leveldb").mkdir(parents=True)
    (profile / "Default/Local Storage/leveldb/000003.log").write_bytes(b"made-up")
    for clutter in _CLUTTER:
        (profile / clutter).parent.mkdir(parents=True, exist_ok=True)
        (profile / clutter).write_bytes(b"made-up")
    outside = root / "outside.sock"
    outside.write_bytes(b"")
    (profile / "SingletonSocket").symlink_to(outside)
    return profile


def _tree(profile: Path) -> set[str]:
    return {path.relative_to(profile).as_posix() for path in profile.rglob("*")}


def _not_kept(profile: Path) -> set[str]:
    """Paths in the profile that the allow-list does not cover."""
    return {
        path
        for path in _tree(profile)
        if path not in {"Default", "downloads", *PROFILE_KEEP}
        and not any(path.startswith(f"{kept}/") for kept in PROFILE_KEEP)
    }


_KEPT_TREE = {
    *PROFILE_KEEP,
    "Default",
    "downloads",
    "Default/Local Storage/leveldb",
    "Default/Local Storage/leveldb/000003.log",
}


@pytest.mark.parametrize(
    "browser",
    [
        pytest.param(_Browser(), id="fetched"),
        pytest.param(_Browser(answer=PlaywrightError("Target closed")), id="failed-mid-fetch"),
        pytest.param(_Browser(launch=PlaywrightError("no 'chrome' channel")), id="not-started"),
    ],
)
async def test_after_any_fetch_the_profile_keeps_only_its_allow_list(
    browser: _Browser, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    profile = _profile(tmp_path)

    with caplog.at_level(logging.INFO):
        await _fetch(browser, profile_dir=profile)

    assert _tree(profile) == _KEPT_TREE
    assert (tmp_path / "outside.sock").exists()
    assert f"profile={profile} removed=10 failed=0" in caplog.text


async def test_a_source_profile_is_trimmed_and_the_others_are_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    profiles = tmp_path / "profiles"
    source = _profile(profiles)  # the folder of the source named "profile"
    sibling = profiles / "wy_wdh" / "Default" / "History"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(b"made-up")

    with caplog.at_level(logging.INFO):
        await _fetch(_Browser(), profile_dir=profiles, profile="profile")

    assert _tree(source) == _KEPT_TREE
    assert sibling.exists()
    assert f"profile={source} removed=10 failed=0" in caplog.text


async def test_a_missing_profile_is_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        result = await _fetch(_Browser(), profile_dir=tmp_path / "profile")

    assert isinstance(result, FetchedDocument)
    assert not (tmp_path / "profile").exists()
    assert "trim" not in caplog.text


async def test_a_profile_entry_that_cannot_be_deleted_is_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    profile = _profile(tmp_path)
    (profile / "Default").chmod(0o500)
    try:
        with caplog.at_level(logging.INFO):
            result = await _fetch(_Browser(), profile_dir=profile)
    finally:
        (profile / "Default").chmod(0o700)

    assert isinstance(result, FetchedDocument)
    assert "Default/History" in _tree(profile)
    assert "Variations" not in _tree(profile)
    assert "could not delete an entry error=PermissionError" in caplog.text
    assert "History" not in caplog.text


# --- live: a real headed Chrome against a local server ------------------------------------

_LIVE_PAGE = b"<html><head><title>Made-up list</title></head><body>Avery Testperson</body></html>"
_LIVE_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
_LIVE_WORKBOOK = b"PK\x03\x04 made-up workbook bytes"
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
    file behind it, ``/list.xlsx`` an attachment behind it, ``/checkbox`` an interactive
    challenge that never clears."""

    def do_GET(self) -> None:
        cleared = "cleared=1" in (self.headers.get("Cookie") or "")
        if self.path == "/checkbox":
            self._send(403, "text/html", _LIVE_TURNSTILE)
        elif not cleared:
            self._send(403, "text/html", _LIVE_CHALLENGE)
        elif self.path == "/list.pdf":
            self._send(200, "application/pdf", _LIVE_PDF)
        elif self.path == "/list.xlsx":
            self._send(
                200,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                _LIVE_WORKBOOK,
                attachment="Made-up list.xlsx",
            )
        else:
            self._send(200, "text/html; charset=utf-8", _LIVE_PAGE)

    def _send(
        self, status: int, content_type: str, body: bytes, *, attachment: str | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if attachment is not None:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
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
async def test_headed_chrome_receives_a_download_behind_a_challenge_and_directly(
    local_site: str, tmp_path: Path
) -> None:
    profile = tmp_path / "profile"
    fetcher = ChromeFetcher(FixedClock(NOW), profile_dir=profile)
    url = f"{local_site}/list.xlsx"

    behind_challenge = await fetcher.fetch(
        url, previous=None, change_signal=ChangeSignal.CONTENT_HASH
    )
    direct = await fetcher.fetch(url, previous=None, change_signal=ChangeSignal.CONTENT_HASH)

    for result in (behind_challenge, direct):
        assert isinstance(result, FetchedDocument), result
        assert (result.body, result.fetched_in_browser) == (_LIVE_WORKBOOK, True)
    assert not any((profile / "downloads").glob("*"))
    assert _not_kept(profile) == set()


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
