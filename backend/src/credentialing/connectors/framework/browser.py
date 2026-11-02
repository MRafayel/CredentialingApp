"""The headed-browser fallback: a :class:`~credentialing.connectors.framework.http.Fetcher`
that runs the installed Google Chrome under Playwright.

Some sites put a bot manager in front of their pages and answer a script with a JavaScript
challenge (Cloudflare's "Just a moment...") that a real browser clears by itself in a few
seconds. A connector that opts in (``ConnectorCapabilities.browser_fallback``) repeats such a
refused request here; see ``framework/base.py``.

How one fetch works, kept to one path for every kind of document:

1. Launch Chrome headed (headless Chrome is rejected by these firewalls) with a persistent
   profile and Chrome's own identity: no User-Agent override, no stealth, nothing hidden.
2. Navigate to the URL and wait while the page is a challenge (:func:`is_challenge`), polling
   until ``challenge_timeout``. Nothing is ever clicked, typed or submitted: a challenge that
   does not clear by itself (an interactive Turnstile checkbox) is ``BLOCKED``.
3. Read the document with ``fetch(url)`` inside the page, so the browser's own connection and
   clearance cookie carry it, and return the server's exact bytes, status and content type.
   This is the same for an HTML page and a file: a PDF opened in Chrome's viewer is not its
   own bytes, so the page's DOM is never used as the body. For an HTML page it costs one more
   request than the navigation, next to the dozens a page load makes anyway.

Change detection is by content hash only (no conditional requests). Every failure is an
outcome, never raised: a launch failure (Chrome missing, Playwright error) is ``FAILED`` with
"browser unavailable"; a refused answer after the challenge is ``BLOCKED``. Page content is
never logged.
"""

import asyncio
import base64
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, Playwright, Response, async_playwright
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
"""How often a challenge page is looked at again while it clears."""

CHALLENGE_STATUSES: Final = frozenset({403, 503})
"""Main-document statuses bot managers serve their challenge with (Cloudflare: 403, 503)."""

CHALLENGE_TITLES: Final = (
    "just a moment",
    "attention required",
    "checking your browser",
    "captcha",
    "verifying",
)
"""Words in a challenge page's ``<title>`` (Cloudflare, Radware, generic CAPTCHA pages)."""

CHALLENGE_MARKERS: Final = (
    "/cdn-cgi/challenge-platform/h/",
    "_cf_chl_opt",
    "cf-challenge",
    "cf-turnstile",
    "challenges.cloudflare.com/turnstile",
)
"""Text only a challenge page carries: Cloudflare's challenge script path and options, and
its Turnstile widget. The bare "challenge-platform" is not one: Cloudflare injects
``/cdn-cgi/challenge-platform/scripts/jsd/`` into ordinary pages (Wyoming's, 2026-10-31)."""

CHALLENGE_HOSTS: Final = ("perfdrive.com",)
"""Hosts that serve only validation pages (Radware's ``validate.perfdrive.com``)."""

_BLOCKING_STATUSES: Final = frozenset({401, 403, 429, 503})
_TITLE: Final = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

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
        max_body_bytes: int = MAX_BODY_BYTES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        playwright: PlaywrightFactory = async_playwright,
    ) -> None:
        self._clock = clock
        self._profile_dir = profile_dir
        self._channel = channel
        self._challenge_polls = max(1, int(challenge_timeout.total_seconds() / POLL_SECONDS))
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
    ) -> FetchOutcome:
        """Read ``url`` in the browser after any challenge clears; unchanged by content hash.

        ``crawl_delay`` is waited once, before the browser starts.
        """
        async with self._lock:
            started = time.perf_counter()
            fetched_at = self._clock.now()
            if crawl_delay > timedelta(0):
                await self._sleep(crawl_delay.total_seconds())
            result = await self._run(url, previous, fetched_at)
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
        self, url: str, previous: SourceSnapshot | None, fetched_at: datetime
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
                        accept_downloads=False,
                        timeout=self._timeout_ms,
                    )
                except Exception as error:  # any launch failure is this fetch's FAILED outcome
                    return failure(Outcome.FAILED, f"browser unavailable: {type(error).__name__}")
                stack.push_async_callback(_close, context)
                return await self._read(context, url, previous, fetched_at)
        except Exception as error:  # a browser failing mid-fetch is FAILED, never raised
            return failure(Outcome.FAILED, f"browser fetch failed: {type(error).__name__}")

    async def _read(
        self,
        context: BrowserContext,
        url: str,
        previous: SourceSnapshot | None,
        fetched_at: datetime,
    ) -> FetchOutcome:
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(self._timeout_ms)
        statuses: list[int] = []

        def on_response(response: Response) -> None:
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                statuses.append(response.status)

        page.on("response", on_response)
        await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        if not await self._challenge_cleared(page, statuses):
            status = statuses[-1] if statuses else None
            shown = f"HTTP {status}" if status is not None else "no HTTP status"
            return FetchFailure(
                outcome=Outcome.BLOCKED,
                status=status,
                note=f"challenge page not cleared ({shown})",
                fetched_at=fetched_at,
            )
        raw = await asyncio.wait_for(
            page.evaluate(_FETCH_IN_PAGE, {"url": url, "max": self._max_body_bytes}),
            timeout=self._timeout,
        )
        return self._outcome(url, _InPageAnswer.model_validate(raw), previous, fetched_at)

    async def _challenge_cleared(self, page: Page, statuses: list[int]) -> bool:
        """Wait, never acting on the page, until it is no longer a challenge or time is up."""
        for poll in range(self._challenge_polls + 1):
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

    def _outcome(
        self,
        url: str,
        answer: _InPageAnswer,
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
        if answer.body is None or answer.size > self._max_body_bytes:
            return failure(
                Outcome.FAILED, f"body of {answer.size} bytes exceeds the cap in the browser"
            )
        body = base64.b64decode(answer.body)
        content_type = answer.content_type
        if (
            content_type is not None
            and "html" in content_type.lower()
            and is_challenge(
                status=status, url=answer.url, html=body.decode("utf-8", errors="replace")
            )
        ):
            return failure(Outcome.BLOCKED, f"challenge page (HTTP {status}) in the browser")
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
            content_type=content_type,
            final_url=answer.url or url,
            byte_size=len(body),
            fetched_at=fetched_at,
            status=status,
            fetched_in_browser=True,
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
