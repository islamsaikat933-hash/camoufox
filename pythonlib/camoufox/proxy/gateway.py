"""
Rotating gateway proxy source.

A rotating gateway (a.k.a. “proxy gateway”, “rotating proxy”) is a single
endpoint — commonly one hostname resolving to many datacenter/residential IPs —
that hands out a different exit IP for every connection. Providers such as
Oxylab's, Luminati/Zyte, Maven, and Webshare all offer one.

Because a fresh connection to the gateway already yields a new exit IP, the
``RotatingGateway`` source simply stamps a unique sticky-session token on each
outgoing request. That serves two purposes:

* Every *browser session* gets a unique session id, so the gateway keeps the
  session pinned to one exit IP for its whole lifetime (no mid-session drift).
* Concurrent sessions never share an exit IP, because each carries its own
  session token.

Mode selection
--------------
``session_mode="per_session"`` (default) mints a brand-new session token for
every session — the recommended behaviour for "a fresh IP for every visit".
``session_mode="static"`` mints one session per gateway entry and reuses it,
which is useful when the gateway charges per session and you only need
per-context identity rather than per-visit rotation.

Real-world gateway strings look like:
    http://user-session:pass@gateway.example.com:8000
    https://user:pass@gateway.example.com:9333
    socks5://user:pass@gateway.example.com:1080
"""
from __future__ import annotations

import itertools
import secrets
from typing import List, Optional

from ..exceptions import InvalidProxy
from .base import ProxySource
from .parsing import ProxyEntry, new_session_for, parse_proxy


class RotatingGateway(ProxySource):
    """
    Rotates exit IPs through a single rotating gateway endpoint.

    Each call to :meth:`next` returns the gateway entry decorated with a fresh
    sticky session, yielding a new exit IP per session.
    """

    def __init__(
        self,
        gateway: str,
        *,
        session_mode: str = "per_session",
        salt: str = "camoufox",
    ) -> None:
        """
        Parameters:
            gateway:
                The gateway endpoint. May include scheme, user, password and
                port (e.g. ``http://user:pass@gateway.example.com:8000``).
            session_mode:
                ``"per_session"`` for a fresh exit IP every session, or
                ``"static"`` to reuse one sticky session per gateway entry.
            salt:
                Mixed into the generated session ids. Set it to a value only
                you know to make colliding with another tenant's session ids
                practically impossible.
        """
        self._gateway = parse_proxy(gateway)
        if self._gateway.host == "localhost" or self._gateway.host.startswith("127."):
            raise InvalidProxy(
                f"Gateway {gateway!r} resolves to a loopback address; "
                "a rotating gateway must be a remote endpoint."
            )
        if session_mode not in ("per_session", "static"):
            raise InvalidProxy(
                f"Invalid session_mode {session_mode!r}. Expected 'per_session' or 'static'."
            )
        self._session_mode = session_mode
        self._salt = salt
        self._static_sessions: Optional[List[ProxyEntry]] = None

    def _mint_session(self, cleartext: bool = True) -> str:
        """Returns a cryptographically random session token."""
        token = secrets.token_hex(8)
        if cleartext:
            return f"st{token}"[:16]
        return token

    def _build_entry(self) -> ProxyEntry:
        return new_session_for(self._gateway, self._mint_session())

    def next(self) -> ProxyEntry:
        if self._session_mode == "per_session":
            return self._build_entry()
        # static mode: build one sticky session per gateway and reuse it
        if self._static_sessions is None:
            self._static_sessions = [self._build_entry()]
        return self._static_sessions[0]

    def description(self) -> str:
        return (
            f"RotatingGateway({self._gateway.host}:{self._gateway.port}, "
            f"mode={self._session_mode})"
        )


def _default_session_mode(gateway: str) -> str:
    """`per_session` unless the operator explicitly pinned one via `--session`."""
    return "per_session"


class RotatingGatewayPool(ProxySource):
    """
    Rotates across several rotating gateways, for resilience.

    Mostly used internally to load-balance across multiple tenant entries of
    the same provider. Rarely needed in user code; prefer :class:`RotatingGateway`.
    """

    def __init__(
        self,
        gateways: List[str],
        *,
        session_mode: str = "per_session",
        salt: str = "camoufox",
    ) -> None:
        if not gateways:
            raise InvalidProxy("RotatingGatewayPool requires at least one gateway")
        self._sources = [RotatingGateway(g, session_mode=session_mode, salt=salt) for g in gateways]
        self._round_robin = itertools.cycle(self._sources)

    def next(self) -> ProxyEntry:
        return next(self._round_robin).next()

    def description(self) -> str:
        return f"RotatingGatewayPool({len(self._sources)} gateways)"