"""
Proxy string parsing and normalization helpers.

Proxy entries come from many providers in many shapes. These helpers
normalize them into the Playwright proxy dict format
(``{"server", "username", "password"}``) that Camoufox already consumes
throughout ``utils.py`` and ``sync_api.py``/``async_api.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional

from ..exceptions import InvalidProxy


@dataclass(frozen=True)
class ProxyEntry:
    """
    A single normalized proxy entry.

    Attributes:
        scheme: One of ``http``, ``https``, ``socks4``, ``socks5``.
        host: The proxy host or IP.
        port: The proxy port.
        username: Optional authentication username.
        password: Optional authentication password.
        session: Optional sticky-session suffix appended to the username.
            Most rotating-gateway providers accept ``user-sESSION`` to pin a
            request to a single exit IP for the session's lifetime.
        raw: The original, untouched input string.
    """

    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    session: Optional[str] = None
    raw: str = field(default="", compare=False)

    @property
    def exit_descriptor(self) -> str:
        """Human-readable identity used for session deduplication."""
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def entry_descriptor(self) -> str:
        """Full identity including session, used to detect same-IP repeats."""
        session = f"/{self.session}" if self.session else ""
        return f"{self.exit_descriptor}{session}"

    def as_playwright_proxy(self) -> Dict[str, str]:
        """
        Returns this entry as a Playwright ``proxy`` option dict.
        """
        proxy: Dict[str, str] = {"server": f"{self.scheme}://{self.host}:{self.port}"}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy

    def as_proxy_string(self, include_creds: bool = True) -> str:
        """
        Returns the entry as a URL-style proxy string.
        """
        server = f"{self.scheme}://{self.host}:{self.port}"
        if not include_creds or not self.username:
            return server
        auth = self.username
        if self.password:
            auth = f"{auth}:{self.password}"
        return server.replace("://", f"://{auth}@", 1)


# Matches user:pass@host:port in any order via a full-string parse.
# Supported forms:
#   host:port
#   scheme://host:port
#   user:pass@host:port
#   scheme://user:pass@host:port
#   scheme://user:pass@host:port?session=xyz
_PROXY_RE = re.compile(
    r"^(?:(?P<scheme>[a-zA-Z][a-zA-Z0-9+\-\.]*)://)?"
    r"(?:(?P<username>[^:@/\s]+)(?::(?P<password>[^@/\s]*))?@)?"
    r"(?P<host>\[[0-9a-fA-F:]+\]|[^:/@\s]+)"
    r"(?::(?P<port>\d{1,5}))?"
    r"(?:\?(?P<query>[^#\s]*))?\s*$"
)

_SUPPORTED_SCHEMES = {"http", "https", "socks4", "socks5"}


def parse_proxy(proxy: str) -> ProxyEntry:
    """
    Parses a single proxy string into a :class:`ProxyEntry`.

    Accepts both standard proxy URLs and the “network-style” forms used by
    rotating providers (``host:port:user:pass``, Maven-style session tokens
    in the username, ``host:port?session=...``). Raises :class:`InvalidProxy`
    when the entry cannot be understood.
    """
    if not proxy or not isinstance(proxy, str):
        raise InvalidProxy(f"Empty or non-string proxy entry: {proxy!r}")

    entry = proxy.strip()
    if not entry:
        raise InvalidProxy(f"Empty proxy entry: {proxy!r}")

    match = _PROXY_RE.match(entry)
    if not match:
        raise InvalidProxy(f"Invalid proxy entry: {proxy!r}")

    scheme = (match.group("scheme") or "http").lower()
    if scheme not in _SUPPORTED_SCHEMES:
        raise InvalidProxy(
            f"Unsupported proxy scheme '{scheme}' in {proxy!r}. "
            f"Supported schemes: {', '.join(sorted(_SUPPORTED_SCHEMES))}"
        )

    host = match.group("host")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    port = match.group("port")
    if port is None:
        raise InvalidProxy(f"Missing port in proxy entry: {proxy!r}")
    port = int(port)
    if not (1 <= port <= 65535):
        raise InvalidProxy(f"Invalid port {port} in proxy entry: {proxy!r}")

    username = match.group("username")
    password = match.group("password")
    session: Optional[str] = None

    # gateway-style session in query string: host:port?session=xyz
    query = match.group("query") or ""
    if query:
        for part in query.split("&"):
            if part.startswith("session="):
                session = part.split("=", 1)[1]

    return ProxyEntry(
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
        session=session,
        raw=proxy,
    )


def parse_proxy_line(line: str) -> ProxyEntry:
    """
    Parses one non-empty, non-comment line from a proxy text file.

    Blank lines and lines whose first non-whitespace character is ``#`` are
    treated as comments and produce ``None``.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        raise ValueError("comment or blank line")
    return parse_proxy(stripped)


def new_session_for(entry: ProxyEntry, session: str) -> ProxyEntry:
    """
    Returns a copy of ``entry`` pinned to the given sticky session.

    The session is encoded in the username as ``username-session`` when
    credentials exist (the convention used by most rotating gateways),
    otherwise in the query string as ``?session=...``.
    """
    session = re.sub(r"[^a-zA-Z0-9_-]", "", session)
    if not session:
        raise InvalidProxy("Session id must contain at least one valid character")
    if entry.username:
        username = f"{entry.username}-{session}"
    else:
        username = session
    return ProxyEntry(
        scheme=entry.scheme,
        host=entry.host,
        port=entry.port,
        username=username,
        password=entry.password,
        session=session,
        raw=entry.raw,
    )