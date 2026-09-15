"""
`BehaviorModel` — per-visitor human behaviour.

Decisions made for every simulated visitor:

* which **entry channel** they come from (direct navigation, a search engine,
  a social/generic referral),
* how many **pages** they visit and in what order,
* how long they **linger** on each page (skewed, bursty dwell times),
* how much they **scroll** (browse depth), and
* whether the visit is a **bounce** (land and leave immediately) or a full
  browsing session.

All values are drawn from skewed distributions — most visitors are quick, a
few stay long — which is how real web analytics data looks.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
class EntryChannel(str, Enum):
    """Where a visitor comes from."""

    DIRECT = "direct"
    SEARCH = "search"       # Google/Bing style referral
    SOCIAL = "social"       # generic social referral
    REFERRAL = "referral"   # other site / link


# --------------------------------------------------------------------------- #
@dataclass
class BehaviorModel:
    """
    Randomised behaviour generator for one human-like visit.

    Parameters:
        max_pages:
            Upper bound on pages viewed in a single session (default 8).
        bounce_rate:
            Probability (0–1) that a visitor leaves after the landing page.
        search_share:
            Probability that a non-direct visitor arrives via search engine.
        social_share:
            Probability that a non-search referral is social.
        dwell_mean / dwell_std:
            Skew-normal-ish dwell time parameters (seconds).
        seed:
            Optional RNG seed.
    """

    max_pages: int = 8
    bounce_rate: float = 0.30
    search_share: float = 0.55
    social_share: float = 0.40
    dwell_mean: float = 45.0
    dwell_std: float = 30.0
    seed: Optional[int] = None

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        if not 0.0 <= self.bounce_rate <= 1.0:
            raise ValueError("bounce_rate must be in [0,1]")
        if not 0.0 <= self.search_share <= 1.0:
            raise ValueError("search_share must be in [0,1]")
        if self.max_pages < 1:
            raise ValueError("max_pages must be >= 1")

    # ------------------------------------------------------------------ #
    def entry_channel(self) -> EntryChannel:
        r = self._rng.random()
        if r < self.search_share:
            return EntryChannel.SEARCH
        if r < self.search_share + (1 - self.search_share) * self.social_share:
            return EntryChannel.SOCIAL
        return EntryChannel.REFERRAL

    def entry_channel_with_direct(self) -> EntryChannel:
        """Picks an entry channel including 'direct' arrivals."""
        channel = self.entry_channel()
        if self._rng.random() < 0.25:  # ~1/4 land without any referrer
            return EntryChannel.DIRECT
        return channel

    # ------------------------------------------------------------------ #
    def page_count(self, allow_single: bool = True) -> int:
        """Sample a page count — most sessions are short."""
        # geometric-ish: P(1)=~0.45, P(2)=~0.25, shrinks after that
        n = 1
        while n < self.max_pages and self._rng.random() < 0.55:
            n += 1
        if not allow_single and n == 1:
            n = 2
        return n

    def is_bounce(self) -> bool:
        return self._rng.random() < self.bounce_rate

    def dwell_time(self) -> float:
        """Skewed dwell time in seconds (clamped to >= 2 s)."""
        t = self._rng.gauss(self.dwell_mean, self.dwell_std)
        return max(2.0, t)

    def scroll_fraction(self) -> float:
        """
        How much of the page the visitor scrolls through (0–1).

        Skewed toward light scrolling — a heavy read is uncommon.
        """
        if self._rng.random() < 0.55:
            return self._rng.uniform(0.05, 0.35)   # quick skim
        if self._rng.random() < 0.8:
            return self._rng.uniform(0.35, 0.8)    # decent read
        return self._rng.uniform(0.8, 1.0)         # full read

    def think_time(self) -> float:
        """Pause between pages — few seconds, sometimes longer (1–6 s)."""
        return self._rng.uniform(1.0, 6.0)

    # ------------------------------------------------------------------ #
    @staticmethod
    def page_order(
        landing: str,
        internal_links: Sequence[str],
        n_pages: int,
    ) -> Tuple[Tuple[str, str], List[str]]:
        """
        Returns ``((landing, link_text), [subsequent_page_urls])``.

        ``link_text`` is a plausible anchor for the referral entry.
        ``subsequent_page_urls`` are picked from ``internal_links`` (backfilled
        with the landing page if the site is tiny).
        """
        link_text = _plausible_anchor(landing)
        pool = list(internal_links)
        if not pool:
            pool = [landing]
        path = []
        for _ in range(n_pages):
            pool = list(set(pool))  # de-dup
            if not pool:
                pool = [landing]
            idx = random.randrange(len(pool))
            path.append(pool.pop(idx))
        return (landing, link_text), path

    def to_dict(self) -> dict:
        return {
            "max_pages": self.max_pages,
            "bounce_rate": self.bounce_rate,
            "search_share": self.search_share,
            "social_share": self.social_share,
            "dwell_mean": self.dwell_mean,
            "dwell_std": self.dwell_std,
        }


# --------------------------------------------------------------------------- #
def _plausible_anchor(url: str) -> str:
    """Makes a human-looking anchor text from a URL."""
    import posixpath
    from urllib.parse import urlparse

    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if not parts:
        words = ["home", "welcome", "overview"]
    else:
        words = [p.replace("-", " ").replace("_", " ").title() for p in parts[-2:]]
    return words[0] if words else "this page"