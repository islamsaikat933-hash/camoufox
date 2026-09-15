"""
`VisitorSession` — one simulated human-like visit.

Two execution modes:

* **Real browser mode** — when a ``session_factory`` callable is supplied, it
  is awaited to get a `(context, page)` pair from a real Camoufox browser
  (optionally through a ``ProxySource`` for per-session IP rotation). The
  visitor then behaves realistically: waits, scrolls, wanders, leaves.

* **Simulated mode** — when no factory is supplied, the same behaviour runs in
  "dry-run" (using sleeps) so the engine and dashboard can be exercised
  without a browser installed.

Either way a :class:`SessionOutcome` records what happened.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from .behavior import BehaviorModel, EntryChannel

# A session factory:  awaitable(ctx_kwargs) -> (context, page)
SessionFactory = Callable[..., Awaitable[Tuple[Any, Any]]]


class SessionOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SessionResult:
    outcome: SessionOutcome
    channel: EntryChannel
    pages_viewed: int
    dwell_seconds: float
    started_at: float
    ended_at: float
    error: Optional[str] = None


# --------------------------------------------------------------------------- #
# tiny sleep helper so the whole timer can be zeroed in tests
async def _sleep(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)


# --------------------------------------------------------------------------- #
class VisitorSession:
    """One human-like visit, either real or simulated."""

    def __init__(
        self,
        visitor_id: int,
        *,
        landing: str,
        internal_links: Optional[List[str]] = None,
        behavior: Optional[BehaviorModel] = None,
        session_factory: Optional[SessionFactory] = None,
        proxy_source: Optional[Any] = None,
        referrer_override: Optional[str] = None,
    ) -> None:
        self.visitor_id = visitor_id
        self.landing = landing
        self.internal_links = list(internal_links or [])
        self.behavior = behavior or BehaviorModel(seed=visitor_id)
        self.session_factory = session_factory
        self.proxy_source = proxy_source
        self.referrer_override = referrer_override
        self._cancelled = False
        self.result: Optional[SessionResult] = None

    # ------------------------------------------------------------------ #
    def channel_and_path(self) -> Tuple[EntryChannel, str, List[str], str]:
        """Plans the visit: channel, landing, path, link text."""
        channel = (
            self.behavior.entry_channel_with_direct()
            if self.referrer_override is None
            else EntryChannel.DIRECT
        )
        n_pages = self.behavior.page_count(allow_single=True)
        (landing, link_text), path = BehaviorModel.page_order(
            self.landing, self.internal_links, n_pages
        )
        return channel, landing, path, link_text

    # ------------------------------------------------------------------ #
    async def run(self) -> SessionResult:
        """Runs the full visit and returns a :class:`SessionResult`."""
        started = time.time()
        channel, landing, path, _link_text = self.channel_and_path()
        try:
            if self.session_factory is None:
                pages = await self._run_simulated(landing)
            else:
                pages = await self._run_browser(channel, landing)
        except asyncio.CancelledError:
            self._cancelled = True
            self.result = SessionResult(
                outcome=SessionOutcome.CANCELLED,
                channel=channel,
                pages_viewed=0,
                dwell_seconds=0.0,
                started_at=started,
                ended_at=time.time(),
            )
            raise
        except Exception as exc:  # noqa: BLE001 — surface to engine
            self.result = SessionResult(
                outcome=SessionOutcome.FAILED,
                channel=channel,
                pages_viewed=0,
                dwell_seconds=0.0,
                started_at=started,
                ended_at=time.time(),
                error=str(exc),
            )
            return self.result

        self.result = SessionResult(
            outcome=SessionOutcome.COMPLETED,
            channel=channel,
            pages_viewed=pages,
            dwell_seconds=time.time() - started,
            started_at=started,
            ended_at=time.time(),
        )
        return self.result

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------------ #
    async def _run_simulated(self, landing: str) -> int:
        """
        Browserless dry-run: the behaviour is identical to the real flow but
        uses sleeps instead of a browser, so timing stays realistic.
        """
        pages = 0
        if self.behavior.is_bounce():
            await _sleep(self.behavior.dwell_time())
            return 1  # landed on one page, then left
        await _sleep(self.behavior.think_time())
        pages += 1
        n_path = self.behavior.page_count(allow_single=False) - 1
        for _ in range(n_path):
            await _sleep(self.behavior.think_time())
            await _sleep(self.behavior.dwell_time() * 0.4)
            pages += 1
        # final read
        await _sleep(self.behavior.dwell_time())
        return pages

    # ------------------------------------------------------------------ #
    async def _run_browser(self, channel: EntryChannel, landing: str) -> int:
        """
        Real (or mockable) browser session.

        ``session_factory`` is awaited to obtain a (context, page) pair. The
        channel controls the HTTP referrer header we set on navigation, so
        analytics on the target site will attribute the visit realistically.

        Returns the number of pages actually loaded.
        """
        kwargs: dict[str, Any] = {}
        if self.proxy_source is not None:
            kwargs["proxy_source"] = self.proxy_source

        context, page = await self.session_factory(**kwargs)

        # Referrer styling: search / social / referral send a referrer header.
        referrer = None
        if channel == EntryChannel.SEARCH:
            referrer = "https://www.google.com/"
        elif channel == EntryChannel.SOCIAL:
            referrer = "https://www.facebook.com/"
        elif channel == EntryChannel.REFERRAL:
            referrer = self.referrer_override or "https://t.co/"

        await _sleep(self.behavior.think_time() * 0.5)

        # first (landing) page
        await page.goto(landing, referrer=referrer)
        await self._humanize_page(page, first=True)
        pages = 1

        if self.behavior.is_bounce():
            await context.close()
            return pages

        # internal wander
        n_path = self.behavior.page_count(allow_single=False) - 1
        path_links = self.internal_links[:n_path] or [landing]
        for url in path_links:
            await _sleep(self.behavior.think_time())
            await page.goto(url, referrer=self.landing)
            await self._humanize_page(page, first=False)
            pages += 1

        await _sleep(self.behavior.dwell_time())
        await context.close()
        return pages

    # ------------------------------------------------------------------ #
    async def _humanize_page(self, page: Any, *, first: bool) -> None:
        """Scrolls with human-like slowness and pauses."""
        try:
            height = await page.evaluate("document.body.scrollHeight || 800")
        except Exception:
            height = 800
        frac = self.behavior.scroll_fraction()
        target = max(50.0, height * frac)
        steps = max(2, int(target / 180))
        for s in range(steps):
            if self._cancelled:
                break
            delta = int(min(180 * (s + 1), target))
            try:
                await page.mouse.wheel(0, delta)
            except Exception:
                pass
            await _sleep(self.behavior.think_time() * 0.25)
        # read pause
        if not first:
            await _sleep(self.behavior.dwell_time() * 0.5)