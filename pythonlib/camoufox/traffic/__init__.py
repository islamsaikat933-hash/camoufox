"""
`camoufox.traffic` — realistic human-traffic simulation for Camoufox.

Design goals:
* **Human-like arrivals** — visitors appear at random times throughout a
  configurable window (default 24h) following a *diurnal* activity curve:
  quiet overnight, busy mid-day, a small evening bump. No bursts, no fixed
  intervals.
* **Human-like behaviour** — each visitor chooses an entry channel
  (direct / search-engine referral / social or generic referral), wanders a
  few pages with realistic dwell times and scroll activity, and leaves —
  sometimes quickly (bounce), sometimes after a long session.
* **Concurrent but bounded** — the engine caps how many visitors roam the
  site simultaneously so hosts don't collapse under a synthetic stampede.
* **GUI dashboard** — a zero-dependency web dashboard (stdlib only) to
  configure a run (visitors, duration, proxy source) and watch live metrics.

The simulator is fully browser-independent: every module is unit-testable
without a Camoufox install. When a Camoufox browser IS available, visitors
open real anti-detect sessions (optionally through the proxy rotation added in
`camoufox.proxy`); otherwise sessions degrade gracefully into simulated
requests so the engine and dashboard still work for dry-runs.

Public entry points:

* :class:`~camoufox.traffic.scheduler.TrafficScheduler` — arrival-time distribution
* :class:`~camoufox.traffic.behavior.BehaviorModel` — per-visitor behaviour
* :class:`~camoufox.traffic.visitor.VisitorSession` — one simulated visitor
* :class:`~camoufox.traffic.engine.TrafficEngine` — the orchestrator
* :func:`~camoufox.traffic.server.serve_dashboard` — run the GUI dashboard
"""
from __future__ import annotations

from .scheduler import TrafficScheduler
from .behavior import BehaviorModel, EntryChannel
from .visitor import VisitorSession, SessionOutcome
from .engine import TrafficEngine, TrafficStats

__all__ = [
    "TrafficScheduler",
    "BehaviorModel",
    "EntryChannel",
    "VisitorSession",
    "SessionOutcome",
    "TrafficEngine",
    "TrafficStats",
]