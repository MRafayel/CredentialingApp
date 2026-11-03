"""The headed-browser fallback: a :class:`~credentialing.connectors.framework.http.Fetcher`
that runs the installed Google Chrome under Playwright.

Some sites put a bot manager in front of their pages and answer a script with a JavaScript
challenge (Cloudflare's "Just a moment...", Radware's "Verifying your browser") that a real
browser clears by itself in a few seconds. A connector that opts in
(``ConnectorCapabilities.browser_fallback``) repeats such a refused request here; see
``framework/base.py``.

How one fetch works:

1. Launch Chrome headed (headless Chrome is rejected by these firewalls) with a persistent
   profile and Chrome's own identity: no User-Agent override, no stealth, nothing hidden.
   Downloads are accepted into ``<profile>/downloads``.
2. With an ``entry_url`` (``ConnectorCapabilities.browser_warm_up``: Kansas's Cloudflare
   refuses a direct file request but, in the survey, served it after the page that links it),
   open that page first and wait for its challenge to clear the same way; one that stays a
   challenge is ``BLOCKED``. Then stay on it as a reader would: wait for its ``load`` event
   (a page still loading at the navigation timeout is waited on no longer), then until the
   firewall's detection script has set its clearance cookie (:data:`CLEARANCE_COOKIES`, which
   Cloudflare sets silently seconds after the page loads), looking at the browser's cookies
   every :data:`POLL_SECONDS` for at most :data:`WARM_UP_TIMEOUT` (without it the fetch goes
   on: other firewalls use other cookies), then :data:`WARM_UP_SECONDS` more. The URL is then
   opened with the entry page as its Referer, as following the page's link would. The page is
   only loaded and its cookies only looked at, never fetched, read, set or acted on. This is
   one extra page load per poll that reaches the browser.
3. Navigate to the URL and wait, polling until ``challenge_timeout``, while the page is a
   challenge (:func:`is_challenge`) and no download has arrived. Nothing is ever clicked, typed
   or submitted: a challenge that does not clear by itself (an interactive checkbox) is
   ``BLOCKED``.
4. Read the document, which comes in one of three shapes:

   - an HTML page, or
   - a document Chrome shows inline (a PDF in its viewer): both are read with ``fetch(url)``
     inside the page, so the browser's own connection and clearance cookie carry it, and the
     server's exact bytes, status and content type come back. A PDF in the viewer is not its
     own bytes, so the page's DOM is never used as the body. For an HTML page it costs one
     more request than the navigation, next to the dozens a page load makes anyway;
   - a download (a workbook served as an attachment): the navigation turns into a download,
     either at once (Playwright then reports the navigation as aborted) or after a challenge
     reloads itself (Minnesota's Radware interstitial, whose DOM may still show while the file
     arrives). The downloaded file is read and deleted before the browser closes.

Change detection is by content hash only (no conditional requests). Every failure is an
outcome, never raised: a launch failure (Chrome missing, Playwright error) is ``FAILED`` with
"browser unavailable"; a refused answer after the challenge is ``BLOCKED``; a navigation that
was aborted for a download that never arrived is ``FAILED``. Page content and downloaded file
names are never logged.

What the profile keeps: after every fetch, success or failure (also when Chrome did not
start), once the browser is closed, everything in the profile is deleted except
:data:`PROFILE_KEEP`: the cookies (a firewall's clearance), the small stores a bot manager may
read (local storage, network state, device-bound sessions) and the files Chrome needs to start
the profile again; ``downloads/`` stays as an empty folder. Caches (page assets, scripts,
favicons, possibly copies of downloaded files), history, sessions and Safe Browsing data go.
A failure to delete is logged by its error class and never changes the fetch's result.
"""

import asyncio
import base64
import hashlib
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from playwright.async_api import (
    BrowserContext,
    Download,
    Page,
    Playwright,
    Response,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, ConfigDict

from credentialing.connectors.framework.http import (
    MAX_BODY_BYTES,
    FetchedDocument,
    FetchFailure,
    FetchOutcome,
    NotModified,
)
from credentialing.domain.enums import ChangeSignal, Outcome
from credentialing.domain.models import SourceSnapshot
from credentialing.domain.ports import Clock

logger = logging.getLogger(__name__)

POLL_SECONDS: Final = 0.5
"""How often a challenge page is looked at again while it clears (or a download arrives)."""

WARM_UP_SECONDS: Final = 10.0
"""Dwell on the entry page once its clearance cookie is set (or the wait for it is over),
before the requested URL: about as long as a person takes to find the link."""

WARM_UP_TIMEOUT: Final = timedelta(seconds=30)
"""Longest wait on the entry page for a clearance cookie."""

CLEARANCE_COOKIES: Final = ("cf_clearance",)
"""Cookies a firewall's detection script sets once the browser has passed its check
(Cloudflare's ``cf_clearance``); their names are looked at, never their values."""

CHALLENGE_STATUSES: Final = frozenset({403, 503})
"""Main-document statuses bot managers serve their challenge with (Cloudflare: 403, 503)."""

CHALLENGE_TITLES: Final = (
    "just a moment",
    "attention required",
    "checking your browser",
    "captcha",
    "verifying",
    "radware",
)
"""Words in a challenge page's ``<title>`` (Cloudflare, Radware's "Radware Page" interstitial
and "Radware Captcha Page", generic CAPTCHA pages)."""

CHALLENGE_MARKERS: Final = (
    "/cdn-cgi/challenge-platform/h/",
    "_cf_chl_opt",
    "cf-challenge",
    "cf-turnstile",
    "challenges.cloudflare.com/turnstile",
    "verifying your browser before proceeding",
)
"""Text only a challenge page carries: Cloudflare's challenge script path and options, its
Turnstile widget, and Radware's interstitial sentence. Keep each one this specific: the bare
"challenge-platform" is not one, because Cloudflare injects
``/cdn-cgi/challenge-platform/scripts/jsd/`` into ordinary pages (Wyoming's, 2026-10-31)."""

CHALLENGE_HOSTS: Final = ("perfdrive.com",)
"""Hosts that serve only validation pages (Radware's ``validate.perfdrive.com``)."""

PROFILE_KEEP: Final = frozenset(
    {
        "Local State",
        "Last Version",
        "Default/Cookies",
        "Default/Cookies-journal",
        "Default/Local Storage",
        "Default/Network Persistent State",
        "Default/Preferences",
        "Default/Secure Preferences",
        "Default/Device Bound Sessions",
        "Default/Device Bound Sessions-journal",
    }
)
"""Paths, relative to the profile, that survive a fetch (a directory with all it holds); the
module docstring says why. Everything else in the profile is deleted once the browser closes."""

PROFILE_DOWNLOADS: Final = "downloads"
"""The profile's download folder: kept, emptied."""

_BLOCKING_STATUSES: Final = frozenset({401, 403, 429, 503})
_TITLE: Final = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DOWNLOAD_ERRORS: Final = ("download is starting", "net::err_aborted")
"""What Playwright says when a navigation ends in a download instead of a page."""

_FETCH_IN_PAGE: Final = """
async ({url, max}) => {
  const response = await fetch(url, {credentials: "include"});
  const bytes = new Uint8Array(await response.arrayBuffer());
  const answer = {
    status: response.status,
    content_type: response.headers.get("content-type"),
    url: response.url,
    size: bytes.length,
    body: null,
  };
  if (bytes.length > max) return answer;
  let text = "";
  for (let i = 0; i < bytes.length; i += 32768) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
  }
  answer.body = btoa(text);
  return answer;
}
"""

type PlaywrightFactory = Callable[[], AbstractAsyncContextManager[Playwright]]


def is_challenge(*, status: int | None, url: str, html: str) -> bool:
    """Whether a page is a bot manager's challenge rather than the document.

    A challenge is served with a challenge status, titled like one, carries a challenge
    marker, or sits on a validation host (the module constants say which).
    """
    if status in CHALLENGE_STATUSES:
        return True
    host = (urlsplit(url).hostname or "").lower()
    if any(host == known or host.endswith(f".{known}") for known in CHALLENGE_HOSTS):
        return True
    found = _TITLE.search(html)
    title = unescape(found[1]).strip().lower() if found else ""
    if any(word in title for word in CHALLENGE_TITLES):
        return True
    text = html.lower()
    return any(marker in text for marker in CHALLENGE_MARKERS)


class _InPageAnswer(BaseModel):
    """What the in-page ``fetch`` returns, validated at the browser boundary."""

    model_config = ConfigDict(frozen=True)

    status: int
    content_type: str | None
    url: str
    size: int
    body: str | None


@dataclass(frozen=True, slots=True)
class _Body:
    """What the browser read, however it came: in the page or as a download."""

    status: int
    content_type: str | None
    url: str
    size: int
    body: bytes | None
    """``None`` when ``size`` is over the cap and the bytes were not read."""
    html: bool
    """Whether the body is an HTML page that might be a challenge."""
    challenge_note: str


class ChromeFetcher:
    """Implements :class:`~credentialing.connectors.framework.http.Fetcher` in headed Chrome.

    One browser at a time (Chrome locks its profile), launched per fetch and always closed.
    """

    def __init__(
        self,
        clock: Clock,
        *,
        profile_dir: Path,
        channel: str = "chrome",
        challenge_timeout: timedelta = timedelta(seconds=45),
        navigation_timeout: timedelta = timedelta(seconds=60),
        warm_up_timeout: timedelta = WARM_UP_TIMEOUT,
        max_body_bytes: int = MAX_BODY_BYTES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        playwright: PlaywrightFactory = async_playwright,
    ) -> None:
        self._clock = clock
        self._profile_dir = profile_dir
        self._channel = channel
        self._challenge_polls = max(1, int(challenge_timeout.total_seconds() / POLL_SECONDS))
        self._warm_up_polls = max(1, int(warm_up_timeout.total_seconds() / POLL_SECONDS))
        self._timeout_ms = navigation_timeout.total_seconds() * 1000
        self._timeout = navigation_timeout.total_seconds()
        self._max_body_bytes = max_body_bytes
        self._sleep = sleep
        self._playwright = playwright
        self._lock = asyncio.Lock()

    async def fetch(
        self,
        url: str,
        *,
        previous: SourceSnapshot | None,
        change_signal: ChangeSignal,
        crawl_delay: timedelta = timedelta(0),
        entry_url: str | None = None,
    ) -> FetchOutcome:
        """Read ``url`` in the browser after any challenge clears; unchanged by content hash.

        ``crawl_delay`` is waited once, before the browser starts, also when ``entry_url``
        (a page opened first in the same session, skipped when it is ``url``) adds a page load.
        """
        async with self._lock:
            started = time.perf_counter()
            fetched_at = self._clock.now()
            if crawl_delay > timedelta(0):
                await self._sleep(crawl_delay.total_seconds())
            warm_up = entry_url if entry_url is not None and entry_url != url else None
            result = await self._run(url, warm_up, previous, fetched_at)
        logger.info(
            "browser fetch url=%s signal=%s result=%s status=%s bytes=%s duration_ms=%d",
            url,
            change_signal.value,
            _describe(result),
            result.status,
            result.byte_size if isinstance(result, FetchedDocument) else "-",
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def _run(
        self,
        url: str,
        entry_url: str | None,
        previous: SourceSnapshot | None,
        fetched_at: datetime,
    ) -> FetchOutcome:
        def failure(outcome: Outcome, note: str) -> FetchFailure:
            return FetchFailure(outcome=outcome, status=None, note=note, fetched_at=fetched_at)

        try:
            async with AsyncExitStack() as stack:
                try:
                    playwright = await stack.enter_async_context(self._playwright())
                    context = await playwright.chromium.launch_persistent_context(
                        user_data_dir=self._profile_dir,
                        channel=self._channel,
                        headless=False,
                        accept_downloads=True,
                        downloads_path=self._profile_dir / "downloads",
                        timeout=self._timeout_ms,
                    )
                except Exception as error:  # any launch failure is this fetch's FAILED outcome
                    return failure(Outcome.FAILED, f"browser unavailable: {type(error).__name__}")
                stack.push_async_callback(_close, context)
                return await self._read(context, url, entry_url, previous, fetched_at)
        except Exception as error:  # a browser failing mid-fetch is FAILED, never raised
            return failure(Outcome.FAILED, f"browser fetch failed: {type(error).__name__}")
        finally:
            await _trim(self._profile_dir)

    async def _read(
        self,
        context: BrowserContext,
        url: str,
        entry_url: str | None,
        previous: SourceSnapshot | None,
        fetched_at: datetime,
    ) -> FetchOutcome:
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(self._timeout_ms)
        statuses: list[int] = []
        downloads: list[Download] = []

        def on_response(response: Response) -> None:
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                statuses.append(response.status)

        def on_download(download: Download) -> None:  # Playwright tags the handler: no builtin
            downloads.append(download)

        page.on("response", on_response)
        page.on("download", on_download)
        if entry_url is not None:
            await page.goto(entry_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            if not await self._settle(page, statuses, downloads, read_page=True):
                return _not_cleared(statuses, fetched_at, where=" on the entry page")
            await self._dwell(context, page, entry_url)
            for download in downloads:  # the entry page is not the document
                await _discard(download)
            statuses.clear()
            downloads.clear()
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self._timeout_ms, referer=entry_url
            )
        except PlaywrightError as error:
            if not downloads and not _became_download(error):
                raise
            # A navigation that became a download left no page to read (about:blank), so
            # the in-page fetch is never tried: only the download can carry the document.
            if not await self._settle(page, statuses, downloads, read_page=False):
                return FetchFailure(
                    outcome=Outcome.FAILED,
                    status=None,
                    note="download did not arrive in the browser",
                    fetched_at=fetched_at,
                )
        else:
            if not await self._settle(page, statuses, downloads, read_page=True):
                return _not_cleared(statuses, fetched_at)
        if downloads:
            body = await self._downloaded(downloads[0], url, fetched_at)
            if isinstance(body, FetchFailure):
                return body
        else:
            raw = await asyncio.wait_for(
                page.evaluate(_FETCH_IN_PAGE, {"url": url, "max": self._max_body_bytes}),
                timeout=self._timeout,
            )
            body = _in_page(_InPageAnswer.model_validate(raw))
        return self._outcome(url, body, previous, fetched_at)

    async def _dwell(self, context: BrowserContext, page: Page, entry_url: str) -> None:
        """Stay on the cleared entry page: its ``load`` event, its clearance cookie, a pause.

        Only waits and looks at cookie names; nothing is sent, set or acted on.
        """
        with suppress(PlaywrightError):  # still loading at the timeout: wait no longer
            await page.wait_for_load_state("load", timeout=self._timeout_ms)
        names: set[str] = set()
        for poll in range(self._warm_up_polls + 1):
            names = {cookie.get("name", "") for cookie in await context.cookies([entry_url])}
            if names.intersection(CLEARANCE_COOKIES):
                break
            if poll < self._warm_up_polls:
                await self._sleep(POLL_SECONDS)
        else:
            logger.debug(
                "browser warm-up without a clearance cookie url=%s cookies=%s",
                entry_url,
                sorted(names),
            )
        await self._sleep(WARM_UP_SECONDS)

    async def _settle(
        self, page: Page, statuses: list[int], downloads: list[Download], *, read_page: bool
    ) -> bool:
        """Wait, never acting on the page, until a download arrives or (with ``read_page``)
        the page is no longer a challenge; ``False`` when time is up."""
        for poll in range(self._challenge_polls + 1):
            if downloads:
                return True
            if read_page:
                try:
                    html = await page.content()
                except PlaywrightError:  # the challenge is navigating to the document
                    html = None
                status = statuses[-1] if statuses else None
                if html is not None and not is_challenge(status=status, url=page.url, html=html):
                    return True
            if poll < self._challenge_polls:
                await self._sleep(POLL_SECONDS)
        return False

    async def _downloaded(
        self, download: Download, url: str, fetched_at: datetime
    ) -> _Body | FetchFailure:
        """Wait for the download to finish and read it, then delete the file."""
        try:
            failed = await asyncio.wait_for(download.failure(), timeout=self._timeout)
            if failed is not None:
                return FetchFailure(
                    outcome=Outcome.FAILED,
                    status=None,
                    note="download failed in the browser",
                    fetched_at=fetched_at,
                )
            path = await asyncio.wait_for(download.path(), timeout=self._timeout)
            size, data = await asyncio.to_thread(_read_capped, path, self._max_body_bytes)
        finally:
            await _discard(download)
        return _Body(
            status=200,
            content_type=None,
            url=download.url or url,
            size=size,
            body=data,
            html=data is not None and _looks_like_html(data),
            challenge_note="challenge page delivered as a download",
        )

    def _outcome(
        self,
        url: str,
        answer: _Body,
        previous: SourceSnapshot | None,
        fetched_at: datetime,
    ) -> FetchOutcome:
        status = answer.status

        def failure(outcome: Outcome, note: str) -> FetchFailure:
            return FetchFailure(outcome=outcome, status=status, note=note, fetched_at=fetched_at)

        if status in _BLOCKING_STATUSES:
            return failure(Outcome.BLOCKED, f"HTTP {status} in the browser")
        if status >= 500:
            return failure(Outcome.SOURCE_UNAVAILABLE, f"HTTP {status} in the browser")
        if not 200 <= status < 300:
            return failure(Outcome.FAILED, f"unexpected HTTP {status} in the browser")
        body = answer.body
        if body is None or answer.size > self._max_body_bytes:
            return failure(
                Outcome.FAILED, f"body of {answer.size} bytes exceeds the cap in the browser"
            )
        if answer.html and is_challenge(
            status=status, url=answer.url, html=body.decode("utf-8", errors="replace")
        ):
            return failure(Outcome.BLOCKED, answer.challenge_note)
        content_hash = hashlib.sha256(body).hexdigest()
        if previous is not None and previous.content_hash == content_hash:
            return NotModified(
                reason="content hash unchanged",
                etag=None,
                last_modified=None,
                fetched_at=fetched_at,
                status=status,
                fetched_in_browser=True,
            )
        return FetchedDocument(
            body=body,
            content_hash=content_hash,
            etag=None,
            last_modified=None,
            content_type=answer.content_type,
            final_url=answer.url or url,
            byte_size=len(body),
            fetched_at=fetched_at,
            status=status,
            fetched_in_browser=True,
        )


def _not_cleared(statuses: list[int], fetched_at: datetime, *, where: str = "") -> FetchFailure:
    """``BLOCKED``: the page stayed a challenge (``where`` names the entry page)."""
    status = statuses[-1] if statuses else None
    shown = f"HTTP {status}" if status is not None else "no HTTP status"
    return FetchFailure(
        outcome=Outcome.BLOCKED,
        status=status,
        note=f"challenge page not cleared{where} ({shown})",
        fetched_at=fetched_at,
    )


def _in_page(answer: _InPageAnswer) -> _Body:
    """The in-page ``fetch`` answer with its body decoded."""
    content_type = answer.content_type
    return _Body(
        status=answer.status,
        content_type=content_type,
        url=answer.url,
        size=answer.size,
        body=None if answer.body is None else base64.b64decode(answer.body),
        html=content_type is not None and "html" in content_type.lower(),
        challenge_note=f"challenge page (HTTP {answer.status}) in the browser",
    )


def _became_download(error: PlaywrightError) -> bool:
    """Whether a navigation error is Playwright saying the navigation became a download."""
    message = str(error).lower()
    return any(text in message for text in _DOWNLOAD_ERRORS)


def _read_capped(path: Path, cap: int) -> tuple[int, bytes | None]:
    """A downloaded file's size and bytes; the bytes are not read when it is over ``cap``."""
    size = path.stat().st_size
    return size, (path.read_bytes() if size <= cap else None)


def _looks_like_html(body: bytes) -> bool:
    """Whether downloaded bytes are an HTML page rather than the file (a sniff, not a parse)."""
    head = body[:1024].removeprefix(b"\xef\xbb\xbf")
    lowered = head.lower()
    return head.lstrip().startswith(b"<") or b"<html" in lowered or b"<!doctype" in lowered


async def _discard(download: Download) -> None:
    """Delete a downloaded file once read; a failure is logged by name, never raised."""
    try:
        await download.delete()
    except Exception as error:  # the context's close removes downloads too
        logger.warning("browser download delete failed error=%s", type(error).__name__)


@dataclass(frozen=True, slots=True)
class ProfileTrim:
    """What :func:`trim_profile` did: entries removed, and the class of each deletion error."""

    removed: int
    errors: tuple[str, ...]


def trim_profile(profile_dir: Path) -> ProfileTrim | None:
    """Delete everything in a closed browser profile but :data:`PROFILE_KEEP`, and empty its
    downloads folder; ``None`` (nothing done) when there is no profile. Symbolic links are
    removed, never followed. A deletion that fails is skipped and its error class recorded."""
    if not profile_dir.is_dir():
        return None
    keep = {Path(path) for path in PROFILE_KEEP}
    ancestors = {parent for path in keep for parent in path.parents if parent != Path()}
    removed = 0
    errors: list[str] = []
    pending = [Path()]
    while pending:
        folder = pending.pop()
        for entry in sorted((profile_dir / folder).iterdir()):
            relative = folder / entry.name
            if relative in keep:
                continue
            is_folder = entry.is_dir() and not entry.is_symlink()
            if is_folder and (relative in ancestors or relative == Path(PROFILE_DOWNLOADS)):
                pending.append(relative)
                continue
            try:
                if is_folder:
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError as error:
                errors.append(type(error).__name__)
            else:
                removed += 1
    return ProfileTrim(removed=removed, errors=tuple(errors))


async def _trim(profile_dir: Path) -> None:
    """Trim the profile off the event loop; a failure is logged by class, never raised."""
    try:
        trimmed = await asyncio.to_thread(trim_profile, profile_dir)
    except Exception as error:  # trimming is housekeeping: it never changes the fetch result
        logger.warning("browser profile trim failed error=%s", type(error).__name__)
        return
    if trimmed is None:
        return
    for name in sorted(set(trimmed.errors)):
        logger.warning("browser profile trim could not delete an entry error=%s", name)
    logger.info(
        "browser profile trimmed profile=%s removed=%d failed=%d",
        profile_dir,
        trimmed.removed,
        len(trimmed.errors),
    )


async def _close(context: BrowserContext) -> None:
    """Close the browser; a failure to close is logged, not allowed to replace the result."""
    try:
        await context.close()
    except Exception as error:  # closing is best effort once the answer is read
        logger.warning("browser close failed error=%s", type(error).__name__)


def _describe(result: FetchOutcome) -> str:
    match result:
        case FetchedDocument():
            return "new"
        case NotModified():
            return "unchanged"
        case FetchFailure():
            return result.outcome.value
