"""
Abstract source of per-session proxies.
"""
from __future__ import annotations

import abc
from typing import Dict, List, Optional

from .parsing import ProxyEntry


class ProxySource(abc.ABC):
    """
    A pluggable source of proxy entries.

    A :class:`ProxySource` hands out one entry per browser session. Two
    concrete implementations ship with Camoufox:

    * :class:`~camoufox.proxy.gateway.RotatingGateway` — a single rotating
      gateway endpoint that yields a new entry IP for every session.
    * :class:`~camoufox.proxy.textfile.TextFileProxy` — a plain-text list of
      proxies, handed out round-robin with automatic skip of dead entries.

    Custom sources subclass this and implement :meth:`next` (optionally also
    :meth:`report_status` to receive health feedback).
    """

    @abc.abstractmethod
    def next(self) -> ProxyEntry:
        """
        Returns the proxy entry to use for the next session.

        Implementations MUST return a new entry on every call. Deduplication
        is handled by the caller (:class:`~camoufox.proxy.manager.ProxyManager`).
        """
        raise NotImplementedError

    def report_status(self, entry: ProxyEntry, ok: bool) -> None:
        """
        Health feedback from the caller.

        Called after a session closes so rotating sources can fail over a dead
        entry, and so managers can surface a dead file entry to the UI. The
        default implementation does nothing.

        Parameters:
            entry: The entry that was used by the session.
            ok: ``True`` when the session completed without proxy errors.
        """
        return None

    def close(self) -> None:
        """Releases any resources held by the source. Default: no-op."""
        return None

    def description(self) -> str:
        """Short human-readable description, used in logs."""
        return self.__class__.__name__


# Re-exported types used across the manager layer to keep import sites short.
PlaywrightProxy = Dict[str, str]
EntryOrNone = Optional[ProxyEntry]
Entries = List[ProxyEntry]