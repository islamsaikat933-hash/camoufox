# Camoufox Real-Human Traffic Simulator

A load/QA testing tool that makes your website look like it is being visited by
real humans — not by a bot stampede.

It spreads visitors across a **24-hour window** at **human-like random times**
(following a real diurnal activity curve), mixes **traffic sources**
(direct / Google search / social / referral), wanders through internal pages
like a person, scrolls at human speed, reads, and leaves.

Built on top of the existing **proxy rotation** feature: every browser session
can exit through a fresh IP.

---

## Quick start

```bash
# 1. Dashboard (GUI) — pick visitors, window and proxy settings in a browser
PYTHONPATH=pythonlib python -m camoufox.traffic
#    then open http://127.0.0.1:8080/

# 2. Headless run from the example
PYTHONPATH=pythonlib python example_traffic_simulator.py \
    --landing https://yoursite.com \
    --visitors 100 \
    --hours 24 \
    --concurrent 8 \
    --gateway "http://user:pass@gateway.example.com:8000"   # optional proxy
```

The GUI exposes the same proxy-rotation options that were added to
`camoufox.proxy` (static proxy URL, text-file pool, rotating gateway) plus the
number of visitors and the time window.

---

## How it works

| Concern | Module | What it does |
|---|---|---|
| **Arrival times** | `traffic/scheduler.py` | Samples `n` arrivals over the window from a weighted diurnal curve (quiet overnight, morning ramp, lunch peak, evening surge). Offsets are random, not fixed intervals. |
| **Who the visitor is** | `traffic/behavior.py` | Chooses entry channel (direct / search / social / referral), page count, dwell time, scroll depth, bounce probability. |
| **One visit** | `traffic/visitor.py` | Executes the visit — referrer header matches the channel, human-slow scroll, internal-page wandering — on a real Camoufox browser session, or simulated if no browser is installed. |
| **Orchestration** | `traffic/engine.py` | Concurrency limiter, launch pacing, per-session `proxy_source` wiring, and live stats. |
| **Dashboard** | `traffic/server.py` + `ui/` | Zero-dependency stdlib HTTP server + SSE dashboard. |

`camoufox.traffic` is **stdlib-only at runtime apart from Camoufox itself**, so
the dashboard runs on any machine that can run Camoufox — including headless
servers.

---

## Dashboard features

* **Run configuration** — target URL, number of visitors, time window (hours),
  maximum simultaneous sessions.
* **Proxy rotation** (per-session) — one of:
  * ⚡ rotating gateway endpoint (`http://user:pass@host:port`),
  * 📄 text file with one proxy per line (round-robin with dead-proxy
    quarantine),
  * 🔌 a static proxy URL.
* **Live traffic panel** — launched / active / completed / failed / pages
  viewed, an arrival-pacing chart, traffic-source breakdown and run metadata.
* Live updates via **Server-Sent Events** — no page reloads.

---

## API (programmatic)

```python
import asyncio
from camoufox.traffic.engine import build_engine_from_config

engine = build_engine_from_config(
    landing="https://yoursite.com",
    visitors=100,
    duration_hours=24,
    max_concurrent=8,
    proxy=None,        # or proxy_file=..., or gateway=...
)

asyncio.run(engine.start())
print(engine.stats.snapshot())
```

Exactly **one** of `proxy` / `proxy_file` / `gateway` may be set — the same
validation as `camoufox.proxy.build_manager_from_config`.

### Stats snapshot

```python
{
    "total_planned": 100,       # visitors scheduled
    "launched": 47,             # sessions launched so far
    "active": 6,                # sessions in flight
    "completed": 40,            # finished visits
    "failed": 1,                # session errors (e.g. bad proxy)
    "cancelled": 0,             # cut short by stop
    "total_pages": 88,          # pages viewed by all visitors
    "dwell_seconds": 1573.2,    # total time spent on the site
    "channels": {"search": 22, "direct": 15, "social": 3, "referral": 7},
    "window_seconds": 86400.0,
    "running": False,
    "progress": 41,
}
```

---

## Running the tests

Unit tests cover the scheduler, behaviour model, visitor session and engine
orchestration — no browser required:

```bash
PYTHONPATH=pythonlib python -m pytest tests/traffic/test_traffic.py
```

---

## Notes

* Without browser binaries installed, sessions run in **simulated mode** (same
  behaviour, sleep-based timing) so the whole tool can be demoed; with
  binaries installed they use real Camoufox contexts.
* Arrival times are timezone-agnostic: hour 0 is the moment the run starts.