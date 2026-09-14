"""
Text-file proxy source.

Reads a plain-text list of proxies (one per line) and hands each one out
round-robin per session. Supports comments (``#``), blank lines, and the
usual proxy formats. The file is re-read on every rotation so entries can be
added/removed externally without restarting the process.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional

from ..exceptions import InvalidProxy
from .base import ProxySource
from .parsing import ProxyEntry, parse_proxy_line


def read_proxy_file(path: str | Path) -> List[ProxyEntry]:
    """
    Reads and parses a proxy text file, ignoring comments and blanks.

    Raises:
        FileNotFoundError: when the file does not exist.
        InvalidProxy: when any line is malformed.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Proxy file not found: {file_path}")

    entries: List[ProxyEntry] = []
    with file_path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                entries.append(parse_proxy_line(stripped))
            except (InvalidProxy, ValueError) as exc:
                raise InvalidProxy(f"Proxy file {file_path}:{lineno}: {exc}") from exc

    if not entries:
        raise InvalidProxy(f"Proxy file {file_path} contains no usable proxies")
    return entries


class TextFileProxy(ProxySource):
    """
    Round-robin proxy source backed by a plain-text file.

    Every call to :meth:`next` advances to the next entry in the file, so each
    browser session uses a distinct proxy from the pool. When a proxy is
    reported dead via :meth:`report_status`, it is moved to a quarantine list
    and skipped until the caller explicitly resets it.

    Circular reuse: when the pool is exhausted (a file with a single proxy),
    the source naturally cycles back to the first entry. Session deduplication
    is enforced upstream by :class:`~camoufox.proxy.manager.ProxyManager`.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        shuffle: bool = False,
        reload_on_rotate: bool = True,
        auto_retry_dead: bool = False,
    ) -> None:
        """
        Parameters:
            path:
                Path to the proxy text file.
            shuffle:
                Shuffle the pool before first use (and on every reload).
            reload_on_rotate:
                Re-read the file on every rotation. Set to ``False`` for a
                fixed snapshot taken at construction time.
            auto_retry_dead:
                If ``True``, proxies reported dead are moved to the back of the
                pool and will be retried later instead of being quarantined
                until the process ends.
        """
        self._path = Path(path)
        self._shuffle = shuffle
        self._reload_on_rotate = reload_on_rotate
        self._auto_retry_dead = auto_retry_dead

        self._lock = threading.RLock()
        self._pool: List[ProxyEntry] = []
        self._index = 0
        self._quarantine: List[ProxyEntry] = []
        self._dead_by_desc = set()

        self._reload()

    # ------------------------------------------------------------------ #
    # pool management
    # ------------------------------------------------------------------ #
    def _reload(self) -> None:
        entries = read_proxy_file(self._path)
        if self._shuffle:
            import random

            random.shuffle(entries)
        with self._lock:
            fresh = [e for e in entries if e.entry_descriptor not in self._dead_by_desc]
            # preserve quarantine across file reloads, keep newly-live entries poolable
            self._pool = fresh
            self._quarantine = [q for q in self._quarantine if q.entry_descriptor in self._dead_by_desc]

    def _next_from_pool(self) -> Optional[ProxyEntry]:
        with self._lock:
            if not self._pool:
                return None
            entry = self._pool[self._index % len(self._pool)]
            self._index += 1
        return entry

    # ------------------------------------------------------------------ #
    # ProxySource
    # ------------------------------------------------------------------ #
    def next(self) -> ProxyEntry:
        if self._reload_on_rotate:
            try:
                self._reload()
            except (FileNotFoundError, InvalidProxy):
                # the file disappeared or has been edited mid-flight: keep
                # serving the in-memory snapshot, it's still valid
                pass
        entry = self._next_from_pool()
        if entry is None:
            raise InvalidProxy(
                f"Proxy pool is empty ({self._path}). All entries are dead "
                "or the file contains no usable proxies."
            )
        return entry

    def report_status(self, entry: ProxyEntry, ok: bool) -> None:
        if ok:
            return
        with self._lock:
            if self._auto_retry_dead:
                # push to back so it is only retried once every other proxy
                try:
                    self._pool.remove(entry)
                except ValueError:
                    pass
                self._pool.append(entry)
            else:
                self._dead_by_desc.add(entry.entry_descriptor)
                if entry not in self._quarantine:
                    self._quarantine.append(entry)

    def dead_proxies(self) -> List[ProxyEntry]:
        """Returns the currently quarantined entries."""
        with self._lock:
            return list(self._quarantine)

    def active_count(self) -> int:
        """Number of live entries left in the pool."""
        with self._lock:
            return len(self._pool)

    def description(self) -> str:
        return f"TextFileProxy({self._path}, entries={self.active_count()})"