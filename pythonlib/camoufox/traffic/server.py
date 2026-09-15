"""
`camoufox.traffic.server` — zero-dependency GUI dashboard.

A tiny stdlib HTTP server (no Flask/FastAPI required) that:

* serves a single-page dashboard (settings form + live stats),
* exposes a small JSON API for starting/stopping a run,
* streams live stats to the page via Server-Sent Events (SSE).

Because it only uses the standard library, the dashboard runs anywhere Camoufox
can, including headless servers. Open ``http://<host>:<port>/`` in any browser
to control a real-human-traffic simulation.

The dashboard is deliberately dependency-light: it manages **one** global
engine instance, which is the right model for a single-machine visitor tool.
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .engine import TrafficEngine, build_engine_from_config

_UI_DIR = Path(__file__).parent / "ui"
# Traffic-dashboard version (independent of the root package version module).
_VER = "1.0.0"


# --------------------------------------------------------------------------- #
# Global state (single run per process — this is a dashboard, not a pool)
# --------------------------------------------------------------------------- #
class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.engine: Optional[TrafficEngine] = None
        self.last_stats: dict = {}
        self.subscribers: list = []       # list of threading.Condition
        self.notifier = threading.Condition(self.lock)
        self.server_since = time.time()

    # -- subscribership ---------------------------------------------- #
    def add_subscriber(self) -> threading.Condition:
        with self.lock:
            cond = threading.Condition(self.lock)
            self.subscribers.append(cond)
            return cond

    def remove_subscriber(self, cond) -> None:
        with self.lock:
            if cond in self.subscribers:
                self.subscribers.remove(cond)

    def publish(self, stats: dict) -> None:
        with self.notifier:
            self.last_stats = stats
            self.notifier.notify_all()

    @property
    def engine_running(self) -> bool:
        with self.lock:
            return bool(self.engine and getattr(self.engine, "running", False))


STATE = _State()


# --------------------------------------------------------------------------- #
def _start_run(params: Dict[str, Any]) -> dict:
    """Validates form params and launches the engine in a background thread."""
    with STATE.lock:
        if STATE.engine and STATE.engine_running:
            return {"ok": False, "error": "A run is already in progress."}

    landing = (params.get("landing") or "").strip().rstrip("/")
    if not landing:
        return {"ok": False, "error": "Target URL is required."}
    if not landing.startswith(("http://", "https://")):
        landing = "https://" + landing

    try:
        visitors = int(params.get("visitors") or 100)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Visitors must be a number."}
    if visitors < 1 or visitors > 1_000_000:
        return {"ok": False, "error": "Visitors must be 1–1,000,000."}

    try:
        duration = float(params.get("duration_hours") or 24.0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Duration must be a number."}
    if not 0.1 <= duration <= 24 * 30:
        return {"ok": False, "error": "Duration must be 0.1–720 hours."}

    try:
        max_concurrent = int(params.get("max_concurrent") or 8)
    except (TypeError, ValueError):
        max_concurrent = 8
    max_concurrent = max(1, min(max_concurrent, 500))

    # proxy options
    proxy = (params.get("proxy") or "").strip() or None
    proxy_file = (params.get("proxy_file") or "").strip() or None
    gateway = (params.get("gateway") or "").strip() or None
    # validate: only one proxy option
    chosen = [p for p in (proxy, proxy_file, gateway) if p]
    if len(chosen) > 1:
        return {"ok": False, "error": "Choose only one proxy option."}

    engine = build_engine_from_config(
        landing=landing,
        visitors=visitors,
        duration_hours=duration,
        max_concurrent=max_concurrent,
        proxy=proxy,
        proxy_file=proxy_file,
        gateway=gateway,
        on_stats=STATE.publish,
    )

    def _run():
        try:
            asyncio_runner(engine)
        except Exception as exc:  # surface fatal errors
            STATE.publish({"fatal": str(exc)})

    with STATE.lock:
        STATE.engine = engine
    t = threading.Thread(target=_run, name="traffic-engine", daemon=True)
    t.start()
    return {"ok": True}


def asyncio_runner(engine: TrafficEngine) -> None:
    """
    Runs an async engine to completion inside a worker thread.

    Used by the dashboard because engines are ``asyncio`` based while the HTTP
    server is threaded.
    """
    import asyncio

    async def _wrap():
        task = engine.start()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_wrap())


# --------------------------------------------------------------------------- #
# HTTP request handling
# --------------------------------------------------------------------------- #
class DashboardHandler(BaseHTTPRequestHandler):
    server_version = f"CamoufoxTraffic/{_VER}"
    protocol_version = "HTTP/1.1"

    # -- helpers ----------------------------------------------------- #
    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, ctype: str = "text/plain", status: int = 200) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_text("404 Not Found", status=404)
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ------------------------------------------------------ #
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        path = url.path

        if path in ("/", "/index.html"):
            self._send_file(_UI_DIR / "index.html")
            return
        if path == "/app.js":
            self._send_file(_UI_DIR / "app.js")
            return
        if path == "/style.css":
            self._send_file(_UI_DIR / "style.css")
            return
        if path == "/api/state":
            self._send_json(self._api_state())
            return
        if path == "/api/events":
            self._stream_events()
            return
        self._send_text("404 Not Found", status=404)

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/api/start":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                params = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                params = parse_qs(raw.decode()) if raw else {}
                params = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}
            result = _start_run(params if isinstance(params, dict) else {})
            self._send_json(result, status=200 if result.get("ok") else 400)
            return
        if url.path == "/api/stop":
            self._stop_run()
            self._send_json({"ok": True})
            return
        self._send_json({"ok": False, "error": "Unknown endpoint"}, status=404)

    # -- logic ------------------------------------------------------- #
    def _api_state(self) -> dict:
        with STATE.lock:
            engine = STATE.engine
            stats = STATE.last_stats
        # Prefer the engine's own live counters once a run has started; fall
        # back to the last published snapshot (or defaults) otherwise.
        if engine is not None:
            state = dict(engine.stats.snapshot())
            state["running"] = engine.running
            state["landing"] = getattr(engine, "landing", "")
            state["total_planned"] = getattr(engine, "n_visitors", 0)
            state["duration_hours"] = getattr(engine, "duration_hours", 0)
            state["max_concurrent"] = getattr(engine, "max_concurrent", 0)
        else:
            state = dict(stats or {})
            state["running"] = False
        state["version"] = _VER
        state["server_since"] = STATE.server_since
        return state

    def _stop_run(self) -> None:
        with STATE.lock:
            engine = STATE.engine
        if engine and engine.running:
            import asyncio

            async def _s():
                await engine.stop()

            asyncio.run(_s())

    # -- SSE --------------------------------------------------------- #
    def _stream_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        cond = STATE.add_subscriber()
        try:
            # initial snapshot
            self._sse_send(STATE.last_stats or {})
            while True:
                with STATE.notifier:
                    STATE.notifier.wait(timeout=2.0)
                self._sse_send(STATE.last_stats or {})
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            STATE.remove_subscriber(cond)

    def _sse_send(self, stats: dict) -> None:
        try:
            data = f"data: {json.dumps(stats)}\n\n"
            self.wfile.write(data.encode())
            self.wfile.flush()
        except Exception:
            raise


# --------------------------------------------------------------------------- #
def serve_dashboard(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False) -> None:
    """
    Blocks and serves the traffic dashboard.

    Parameters:
        host: bind address (``0.0.0.0`` to expose on LAN).
        port: HTTP port.
        open_browser: try to open a browser tab (best-effort).
    """
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"\n  Camoufox Traffic Dashboard  —  http://{host}:{port}/")
    print("  (Ctrl+C to stop)\n")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(f"http://{host}:{port}/")
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard…")
    finally:
        server.server_close()
        # best-effort stop of a running engine
        with STATE.lock:
            engine = STATE.engine
        if engine and engine.running:
            import asyncio

            try:
                asyncio.run(engine.stop())
            except Exception:
                pass


# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None) -> int:
    """CLI entry point: ``python -m camoufox.traffic [--host] [--port]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m camoufox.traffic", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("CAMOUFOX_TRAFFIC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CAMOUFOX_TRAFFIC_PORT", "8080")))
    parser.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = parser.parse_args(argv)

    serve_dashboard(host=args.host, port=args.port, open_browser=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())