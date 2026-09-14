"""
Tests for the Camoufox proxy rotation package.

These are pure unit tests: no browser is required, only the parsing and
rotation logic of :mod:`camoufox.proxy`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from camoufox.exceptions import InvalidProxy
from camoufox.proxy import (
    ProxyEntry,
    ProxyManager,
    RotatingGateway,
    TextFileProxy,
    parse_proxy,
)
from camoufox.proxy.parsing import new_session_for, parse_proxy_line
from camoufox.proxy.textfile import read_proxy_file


@pytest.fixture()
def proxy_file(tmp_path: Path) -> Path:
    """A small text-file proxy pool used by the text-file tests."""
    path = tmp_path / "proxies.txt"
    path.write_text(
        "1.2.3.4:8080\n"
        "http://user:pass@5.6.7.8:3128\n"
        "socks5://9.9.9.9:1080\n"
        "# a comment line\n"
        "\n"
        "https://a:b@10.0.0.1:443\n"
    )
    return path


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
class TestParsing:
    def test_parse_simple(self) -> None:
        entry = parse_proxy("1.2.3.4:8080")
        assert entry.scheme == "http"
        assert entry.host == "1.2.3.4"
        assert entry.port == 8080
        assert entry.username is None

    def test_parse_full(self) -> None:
        entry = parse_proxy("http://user:pass@5.6.7.8:3128")
        assert entry.scheme == "http"
        assert entry.username == "user"
        assert entry.password == "pass"
        assert entry.as_proxy_string() == "http://user:pass@5.6.7.8:3128"

    def test_parse_socks5(self) -> None:
        entry = parse_proxy("socks5://u:p@9.9.9.9:1080")
        assert entry.scheme == "socks5"
        assert entry.host == "9.9.9.9"
        assert entry.port == 1080

    def test_parse_ipv6_brackets(self) -> None:
        entry = parse_proxy("[2001:db8::1]:3128")
        assert entry.host == "2001:db8::1"
        assert entry.port == 3128

    def test_parse_missing_port_rejected(self) -> None:
        with pytest.raises(InvalidProxy):
            parse_proxy("1.2.3.4")

    def test_parse_invalid_scheme_rejected(self) -> None:
        with pytest.raises(InvalidProxy):
            parse_proxy("ftp://1.2.3.4:21")

    def test_parse_garbage_rejected(self) -> None:
        with pytest.raises(InvalidProxy):
            parse_proxy("not a proxy!!!")
        with pytest.raises(InvalidProxy):
            parse_proxy("")

    def test_as_playwright_proxy_shape(self) -> None:
        proxy = parse_proxy("http://u:p@5.6.7.8:3128").as_playwright_proxy()
        assert proxy == {
            "server": "http://5.6.7.8:3128",
            "username": "u",
            "password": "p",
        }

    def test_parse_line_comments(self) -> None:
        with pytest.raises(ValueError):
            parse_proxy_line("# comment")
        with pytest.raises(ValueError):
            parse_proxy_line("   ")


# --------------------------------------------------------------------------- #
# Gateway rotation
# --------------------------------------------------------------------------- #
class TestRotatingGateway:
    def test_every_session_new_entry(self) -> None:
        gateway = RotatingGateway("http://user:pass@gw.example.com:8000")
        e1, e2, e3 = gateway.next(), gateway.next(), gateway.next()
        # unique sticky sessions => unique exit IPs from the provider
        assert e1.username != e2.username
        assert e2.username != e3.username
        assert e3.username != e1.username
        # server portion stays the same
        assert e1.host == e2.host == e3.host

    def test_static_mode_reuses_session(self) -> None:
        gateway = RotatingGateway(
            "http://user:pass@gw.example.com:8000", session_mode="static"
        )
        e1, e2 = gateway.next(), gateway.next()
        assert e1.username == e2.username

    def test_invalid_session_mode(self) -> None:
        with pytest.raises(InvalidProxy):
            RotatingGateway("http://u:p@gw.example.com:8000", session_mode="banana")

    def test_loopback_rejected(self) -> None:
        with pytest.raises(InvalidProxy):
            RotatingGateway("http://u:p@localhost:8000")


# --------------------------------------------------------------------------- #
# Text-file rotation
# --------------------------------------------------------------------------- #
class TestTextFileProxy:
    def test_reads_entries(self, proxy_file: Path) -> None:
        entries = read_proxy_file(proxy_file)
        assert len(entries) == 4  # comments + blanks skipped

    def test_round_robin(self, proxy_file: Path) -> None:
        src = TextFileProxy(proxy_file)
        mgr = ProxyManager(src)
        hosts = [mgr.next().host for _ in range(8)]
        expected = ["1.2.3.4", "5.6.7.8", "9.9.9.9", "10.0.0.1"] * 2
        assert hosts == expected

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_proxy_file(tmp_path / "nope.txt")

    def test_dead_proxy_quarantine(self, proxy_file: Path) -> None:
        src = TextFileProxy(proxy_file)
        mgr = ProxyManager(src)
        first = mgr.next()
        mgr.report_status(first, ok=False)
        assert first in src.dead_proxies()

    def test_auto_retry_dead(self, proxy_file: Path) -> None:
        src = TextFileProxy(proxy_file, auto_retry_dead=True)
        first = src.next()
        src.report_status(first, ok=False)
        # moved to the back — count unchanged
        assert src.active_count() == 4

    def test_quarantine_survives_reload(self, proxy_file: Path) -> None:
        """A dead proxy stays quarantined even after the file is reloaded."""
        src = TextFileProxy(proxy_file)
        first = src.next()
        src.report_status(first, ok=False)

        # force a reload by rotating (default reload_on_rotate=True)
        for _ in range(src.active_count() + 1):
            src.next()

        # the dead host must not be handed out again
        live = src.active_count()
        entries = [src.next().host for _ in range(live + 1)]
        first_host = first.host
        assert first_host not in entries

    def test_empty_pool_raises(self, tmp_path: Path) -> None:
        """A pool whose entries are all dead raises InvalidProxy."""
        path = tmp_path / "one.txt"
        path.write_text("1.2.3.4:8080\n")
        src = TextFileProxy(path)
        entry = src.next()
        src.report_status(entry, ok=False)
        with pytest.raises(InvalidProxy):
            src.next()


# --------------------------------------------------------------------------- #
# ProxyManager orchestration
# --------------------------------------------------------------------------- #
class TestProxyManager:
    def test_tracks_history(self) -> None:
        gateway = RotatingGateway("http://u:p@gw.example.com:8000")
        mgr = ProxyManager(gateway)
        mgr.next()
        mgr.next()
        assert len(mgr.snapshot()) == 2
        mgr.close()

    def test_rejects_non_source(self) -> None:
        with pytest.raises(TypeError):
            ProxyManager("http://u:p@1.2.3.4:8080")  # type: ignore[arg-type]

    def test_session_stamping(self) -> None:
        """new_session_for pins a gateway entry to a sticky session."""
        entry = parse_proxy("http://user:pass@gw.example.com:8000")
        stamped = new_session_for(entry, "abc123")
        assert stamped.username == "user-abc123"
        assert stamped.password == "pass"
        assert stamped.entry_descriptor != entry.entry_descriptor