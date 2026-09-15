"""
`TrafficEngine` — the orchestrator.

Responsibilities:

* owns a :class:`TrafficScheduler` and launches each visitor at its planned
  arrival offset,
* caps **concurrent** sessions so a synthetic surge never hammers the target
  site,
* tracks rolling metrics (sent, active, completed, failed, dwell, channels),
* supports ``start()`` / ``stop()`` from a web dashboard or CLI,
* emits a lightweight event stream for live UI updates via `async_callback`.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .behavior import BehaviorModel, EntryChannel
from .scheduler import TrafficScheduler
from .visitor import SessionOutcome, SessionResult, VisitorSession

StatsCallback = Callable[[dict], Awaitable[None]]


@dataclass
class TrafficStats:
    """Rolling summary for dashboards / logs."""

    total_planned: int = 0
    launched: int = 0
    active: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    total_pages: int = 0
    dwell_seconds: float = 0.0
    channels: Dict[str, int] = field(default_factory=dict)
    started_at: float = 0.0
    window_seconds: float = 0.0
    running: bool = False

    def snapshot(self) -> dict:
        return {
            "total_planned": self.total_planned,
            "launched": self.launched,
            "active": self.active,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "total_pages": self.total_pages,
            "dwell_seconds": round(self.dwell_seconds, 1),
            "channels": dict(self.channels),
            "started_at": self.started_at,
            "window_seconds": self.window_seconds,
            "running": self.running,
            "progress": self.completed + self.failed + self.cancelled,
        }


# --------------------------------------------------------------------------- #
class TrafficEngine:
    """
    Runs a batch of human-like visitors against a target site.

    Bean-counting aside, it is deliberately simple:

    * ``start()`` schedules all visitors; each waits for its arrival time,
      acquires the concurrency slot, runs its session, releases the slot.
    * ``stop()`` cancels queued visitors and waits for running ones to finish.

    Callbacks:
        ``on_stats`` — awaited with a stats snapshot after each visitor ends
        (used by the web dashboard to push live updates).
    """

    def __init__(
        self,
        landing: str,
        n_visitors: int = 100,
        duration_hours: float = 24.0,
        *,
        internal_links: Optional[List[str]] = None,
        max_concurrent: int = 8,
        behavior: Optional[BehaviorModel] = None,
        scheduler: Optional[TrafficScheduler] = None,
        session_factory: Optional[Callable] = None,
        proxy_source: Optional[Any] = None,
        on_stats: Optional[StatsCallback] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.landing = landing
        self.n_visitors = int(n_visitors)
        self.duration_hours = float(duration_hours)
        self.internal_links = list(internal_links or [])
        self.max_concurrent = max(1, int(max_concurrent))
        self.behavior = behavior or BehaviorModel()
        self.scheduler = scheduler or TrafficScheduler(
            self.n_visitors, self.duration_hours, seed=seed
        )
        self.session_factory = session_factory
        self.proxy_source = proxy_source
        self.on_stats = on_stats

        self.stats = TrafficStats(total_planned=self.n_visitors)
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stop_requested = False

    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------ #
    def start(self) -> "asyncio.Task":
        """Starts the engine. Safe to call once; returns the supervisor task."""
        if self.running:
            raise RuntimeError("engine already running")
        self._stop_evt.clear()
        self._stop_requested = False
        self.stats.started_at = time.time()
        self.stats.window_seconds = self.duration_hours * 3600
        self.stats.running = True
        self._task = asyncio.create_task(self._supervise())
        return self._task

    async def stop(self) -> None:
        """Stops new launches and waits for active visitors to drain."""
        self._stop_requested = True
        self._stop_evt.set()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.stats.running = False
        if self.on_stats:
            await self._emit()

    # ------------------------------------------------------------------ #
    async def _supervise(self) -> None:
        offsets = self.scheduler.sample_offsets()
        t0 = time.time()
        tasks: List[asyncio.Task] = []
        try:
            for i, offset in enumerate(offsets):
                if self._stop_evt.is_set():
                    break
                # wait until this visitor's arrival time
                delay = t0 + offset - time.time()
                if delay > 0:
                    try:
                        await asyncio.wait_for(
                            self._stop_evt.wait(), timeout=delay
                        )
                        break  # stop signal while waiting
                    except asyncio.TimeoutError:
                        pass
                tasks.append(
                    asyncio.create_task(self._run_visitor(i, offset))
                )
        finally:
            # drain active sessions
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.stats.running = False
            await _close_browser()
            if self.on_stats:
                await self._emit()

    # ------------------------------------------------------------------ #
    async def _run_visitor(self, vid: int, offset: float) -> None:
        async with self._sem:
            if self._stop_requested and vid > self.stats.launched:
                # don't start brand-new visitors after a stop request
                pass
            session = VisitorSession(
                vid,
                landing=self.landing,
                internal_links=self.internal_links,
                behavior=self.behavior,
                session_factory=self.session_factory,
                proxy_source=self.proxy_source,
            )
            self.stats.launched += 1
            self.stats.active += 1
            try:
                result = await session.run()
            finally:
                self.stats.active -= 1

            self._record(result)
            if self.on_stats:
                await self._emit()

    def _record(self, result: SessionResult) -> None:
        outcome = result.outcome
        if outcome == SessionOutcome.COMPLETED:
            self.stats.completed += 1
            self.stats.total_pages += result.pages_viewed
            self.stats.dwell_seconds += result.dwell_seconds
        elif outcome == SessionOutcome.FAILED:
            self.stats.failed += 1
        else:
            self.stats.cancelled += 1
        key = result.channel.value if isinstance(result.channel, EntryChannel) else str(result.channel)
        self.stats.channels[key] = self.stats.channels.get(key, 0) + 1

    # ------------------------------------------------------------------ #
    async def _emit(self) -> None:
        cb = self.on_stats
        if cb:
            try:
                await cb(self.stats.snapshot())
            except Exception:
                pass


# --------------------------------------------------------------------------- #
async def _default_session_factory(**kwargs):
    """
    Default visitor session factory.

    Returns a real ``(context, page)`` pair when a Camoufox browser is
    available; otherwise a lightweight fake pair so the engine and dashboard
    work anywhere.

    The visitor closes ``context`` when it is done.
    """
    if _browser_available():
        browser = await _get_or_create_browser()
        context = await browser.new_context(**kwargs)
        page = await context.new_page()
        return context, page
    return FakeContext(), FakePage()


def _browser_available() -> bool:
    """True only when a real Camoufox browser binary is usable."""
    try:
        from camoufox.async_api import AsyncCamoufox  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
        from camoufox.pkgman import INSTALL_DIR  # noqa: F401
    except Exception:
        return False
    # A browser is only usable if at least one build has been fetched.
    try:
        install_dir = INSTALL_DIR
        if not (install_dir and install_dir.exists()):
            return False
        return any(install_dir.rglob("firefox*")) or any(install_dir.rglob("camoufox*"))
    except Exception:
        return False


_browser_cm = None  # AsyncCamoufox instance
_browser_singleton = None


async def _get_or_create_browser():
    """
    Lazily launches a shared AsyncCamoufox browser and keeps it alive across
    all visits in a run. Closed via :func:`_close_browser`.
    """
    global _browser_cm, _browser_singleton
    if _browser_singleton is None:
        from camoufox.async_api import AsyncCamoufox

        _browser_cm = AsyncCamoufox(headless=True)
        _browser_singleton = await _browser_cm.__aenter__()
    return _browser_singleton


async def _close_browser():
    global _browser_cm, _browser_singleton
    cm, _browser_singleton = _browser_cm, None
    _browser_cm = None
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
def build_engine_from_config(
    *,
    landing: str,
    visitors: int = 100,
    duration_hours: float = 24.0,
    max_concurrent: int = 8,
    query: Optional[str] = None,
    proxy: Optional[str] = None,
    proxy_file: Optional[str] = None,
    gateway: Optional[str] = None,
    seed: Optional[int] = None,
    on_stats: Optional[StatsCallback] = None,
) -> TrafficEngine:
    """
    CLI/dashboard-friendly factory.

    Accepts the same proxy options as
    ``camoufox.proxy.build_manager_from_config`` (``proxy``, ``proxy_file``,
    ``gateway``) and wires them into a per-visitor ``proxy_source``, so every
    browser session gets a fresh exit IP. On systems without a browser it
    degrades gracefully to simulated sessions.
    """
    from ..proxy.manager import build_manager_from_config

    proxy_source: Optional[Any] = None
    manager = build_manager_from_config(
        proxy=proxy, proxy_file=proxy_file, gateway=gateway
    )
    if manager is not None:
        proxy_source = manager.source

    async def factory(**kwargs):
        kw = dict(kwargs)
        if proxy_source is not None:
            kw["proxy_source"] = proxy_source
        return await _default_session_factory(**kw)

    return TrafficEngine(
        landing,
        n_visitors=visitors,
        duration_hours=duration_hours,
        max_concurrent=max_concurrent,
        session_factory=factory,
        proxy_source=proxy_source,
        on_stats=on_stats,
        seed=seed,
    )


# --------------------------------------------------------------------------- #
class FakeContext:
    """Fake Playwright-style context used when no browser is installed."""

    def __init__(self) -> None:
        self._closed = False

    async def new_page(self):
        return FakePage()

    async def close(self) -> None:
        self._closed = True


class FakePage:
    """Fake Playwright-ish page for dry-run engines."""

    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.url = "about:blank"

    async def goto(self, url: str, *, referrer: Optional[str] = None) -> None:
        self.url = url

    async def evaluate(self, expr: str):
        if "scrollHeight" in expr:
            return 900
        return None


class FakeMouse:
    async def wheel(self, dx: int, dy: int) -> None:
        pass