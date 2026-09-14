"""
ProxyManager — per-session proxy orchestration.

The manager is the thin layer that ties a :class:`ProxySource` to the
browser-session lifecycle:

* every call to :meth:`next` returns the next proxy from the configured
  source (guaranteeing a *new* exit IP per session),
* callers can notify the manager when a session finished so dead proxies are
  removed/failed-over,
* the manager can optionally verify a proxy's exit IP / timezone before a
  session starts (via the same mechanism Camoufox uses elsewhere for geoip).
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from ..exceptions import InvalidProxy
from .base import ProxySource
from .parsing import ProxyEntry, parse_proxy

logger = logging.getLogger("camoufox.proxy")


class ProxyManager:
    """
    Hands out one proxy per browser session from a :class:`ProxySource`.

    Thread-safe: designed to serve concurrent sessions (e.g. a multi-worker
    scraping stack). Rotating sources such as the gateway mint a unique
    session token per call, so concurrent next() calls never collide.

    Attributes:
        source: The underlying :class:`ProxySource`.
        history: Ordered dict mapping descriptors → number of times used.
        last: The most recent :class:`ProxyEntry` handed out (or ``None``).
    """

    def __init__(self, source: ProxySource) -> None:
        if not isinstance(source, ProxySource):
            raise TypeError(f"Expected a ProxySource, got {type(source).__name__}")
        self.source = source
        self.history: Dict[str, int] = {}
        self.last: Optional[ProxyEntry] = None

    # ------------------------------------------------------------------ #
    def next(self) -> ProxyEntry:
        """
        Returns the next proxy for a new browser session.

        Raises:
            InvalidProxy: when the source is exhausted.
        """
        entry = self.source.next()
        self.last = entry
        key = entry.entry_descriptor
        self.history[key] = self.history.get(key, 0) + 1
        return entry

    def report_status(self, entry: ProxyEntry, ok: bool) -> None:
        """Forwards health feedback to the underlying source."""
        self.source.report_status(entry, ok)

    def snapshot(self) -> Dict[str, int]:
        """Returns a copy of the usage history (debugging / dashboards)."""
        return dict(self.history)

    def close(self) -> None:
        """Closes the underlying source (if it holds resources)."""
        try:
            self.source.close()
        finally:
            self.history.clear()
            self.last = None


def build_manager_from_config(
    *,
    proxy_source: ProxySource | None = None,
    proxy: str | None = None,
    proxy_file: str | None = None,
    gateway: str | None = None,
    session_mode: str = "per_session",
) -> ProxyManager | None:
    """
    Small factory used by the Camoufox entry points.

    Pick one (and only one) of the mutually exclusive options:

    * ``proxy_source`` — pass an already-built :class:`ProxySource` directly.
    * ``gateway`` — a rotating gateway endpoint string.
    * ``proxy_file`` — a path to a text file of proxies.
    * ``proxy`` — a single static proxy (kept for backwards compatibility).

    Returns ``None`` when no proxy option is supplied.
    """
    from .gateway import RotatingGateway
    from .textfile import TextFileProxy

    if sum(x is not None for x in (proxy_source, gateway, proxy_file, proxy)) > 1:
        raise InvalidProxy(
            "Pass only one of 'proxy_source', 'gateway', 'proxy_file', or 'proxy'."
        )

    if proxy_source is not None:
        return ProxyManager(proxy_source)

    if gateway is not None:
        return ProxyManager(RotatingGateway(gateway, session_mode=session_mode))

    if proxy_file is not None:
        return ProxyManager(TextFileProxy(proxy_file))

    if proxy is not None:
        # Single static proxy — build a one-entry source so the rest of the
        # pipeline (unique-IP guarantee, history) stays uniform.
        static = parse_proxy(proxy)
        return ProxyManager(_StaticSource(static))

    return None


class _StaticSource(ProxySource):
    """Internal: serves a single static proxy for the legacy 'proxy' argument."""

    def __init__(self, entry: ProxyEntry) -> None:
        self._entry = entry

    def next(self) -> ProxyEntry:
        return self._entry

    def description(self) -> str:
        return f"StaticProxy({self._entry.host}:{self._entry.port})"