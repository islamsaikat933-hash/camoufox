"""
`TrafficScheduler` — human-like arrival times.

Visitors are spread over a configurable window (default 24 h) following a
*diurnal activity curve*. The curve models a real internet population:

* 00:00–06:00  — very quiet (sleep hours)
* 08:00–10:00  — morning ramp-up
* 12:00–14:00  — lunch peak
* 17:00–19:00  — evening bump
* 23:00–24:00  — wind-down

Arrival offsets are sampled from this weighted distribution instead of a
uniform/poisson model, so a run of 100 visitors looks like a *day on a small
website* rather than a metronome or a flash crowd.

Everything is timezone-agnostic: offsets are seconds-from-start, so the caller
decides what ``hour 0`` means.
"""
from __future__ import annotations

import random
from bisect import bisect_left
from typing import List, Optional, Sequence


# --------------------------------------------------------------------------- #
# Diurnal activity curve
# --------------------------------------------------------------------------- #
def _diurnal_curve() -> List[tuple[float, float]]:
    """
    Returns the standard 24h activity curve as (hour, relative_weight) pairs.

    Weights are hand-tuned to resemble aggregate web traffic (desktop+mobile):
    quiet overnight, a morning ramp, a lunch peak, an evening surge and a
    post-dinner wind-down.
    """
    return [
        (0.0, 0.03),
        (5.0, 0.02),
        (6.0, 0.05),
        (7.0, 0.12),
        (9.0, 0.20),
        (12.0, 0.26),
        (14.0, 0.22),
        (17.0, 0.30),
        (20.0, 0.24),
        (22.0, 0.14),
        (23.0, 0.08),
        (24.0, 0.05),
    ]


# --------------------------------------------------------------------------- #
class TrafficScheduler:
    """
    Samples human-like arrival offsets (seconds) for a batch of visitors.

    Parameters:
        n_visitors:
            Total visitors to schedule.
        duration_hours:
            Time window in hours (default 24).
        curve:
            Optional ``(hour, weight)`` pairs to override the default diurnal
            curve. ``hour`` must be within ``[0, duration_hours]``.
        seed:
            Optional RNG seed for reproducible tests.
    """

    # number of samples used to build the piecewise-linear CDF
    _CDF_SAMPLES = 2000

    def __init__(
        self,
        n_visitors: int,
        duration_hours: float = 24.0,
        curve: Optional[Sequence[tuple[float, float]]] = None,
        seed: Optional[int] = None,
    ) -> None:
        if n_visitors < 1:
            raise ValueError("n_visitors must be >= 1")
        if duration_hours <= 0:
            raise ValueError("duration_hours must be > 0")
        self.n_visitors = int(n_visitors)
        self.duration_hours = float(duration_hours)
        self._rng = random.Random(seed)
        if curve:
            self._curve = list(curve)
        else:
            # Scale the canonical 24h curve to the requested window so that
            # arrivals always fall inside ``[0, duration_hours)``.
            self._curve = self._scale_curve(_diurnal_curve())
        self._xs, self._cdf, self._total = self._build_cdf()

    # ------------------------------------------------------------------ #
    def _scale_curve(self, curve: Sequence[tuple[float, float]]) -> List[tuple[float, float]]:
        """Maps a 24h activity curve onto ``self.duration_hours``."""
        if self.duration_hours == 24.0:
            return list(curve)
        k = self.duration_hours / 24.0
        return [(hour * k, weight) for hour, weight in curve]

    # ------------------------------------------------------------------ #
    def _build_cdf(self) -> tuple[List[float], List[float], float]:
        """
        Discretises the activity curve into (hour, cumulative_weight) pairs.

        The piecewise-linear curve is sampled ``_CDF_SAMPLES`` times; each
        sample contributes ``y * dt`` to the running total. Inversion then
        picks an x whose empirical density matches the curve shape.
        """
        xs = [p[0] for p in self._curve]
        ys = [p[1] for p in self._curve]
        if not (all(xs[i] < xs[i + 1] for i in range(len(xs) - 1))):
            raise ValueError("curve hours must be strictly increasing")
        if any(y < 0 for y in ys):
            raise ValueError("curve weights must be non-negative")

        x_min, x_max = xs[0], xs[-1]
        samples = self._CDF_SAMPLES
        points_x: List[float] = []
        points_cdf: List[float] = []
        total = 0.0
        prev_x = x_min
        for i in range(1, samples + 1):
            x = x_min + (x_max - x_min) * i / samples
            y = self._interp(xs, ys, x)
            total += y * (x - prev_x)
            points_x.append(x)
            points_cdf.append(total)
            prev_x = x
        if total <= 0:
            raise ValueError("activity curve has zero total weight")
        return points_x, points_cdf, total

    @staticmethod
    def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
        """Linear interpolation of y at x (clamped at extremes)."""
        j = bisect_left(xs, x)
        if j == 0:
            return ys[0]
        if j >= len(xs):
            return ys[-1]
        x0, x1 = xs[j - 1], xs[j]
        y0, y1 = ys[j - 1], ys[j]
        t = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
        return y0 + t * (y1 - y0)

    # ------------------------------------------------------------------ #
    def sample_offsets(self) -> List[float]:
        """
        Returns ``n_visitors`` arrival offsets in seconds, sorted ascending.

        Uses inverse-CDF sampling of the weighted curve — the shape of arrivals
        follows the activity curve exactly, but individual offsets are random.

        For very short windows (<= 2h) the diurnal shape is meaningless (a
        "night" then takes a few minutes) and users expect to see traffic
        almost immediately, so the offsets are drawn uniformly across the
        window instead.
        """
        offsets = [self._sample_one() for _ in range(self.n_visitors)]
        offsets.sort()
        return offsets

    def _sample_one(self) -> float:
        """Draws one arrival offset (seconds from start) by inverse-CDF."""
        if self.duration_hours <= 2.0:
            return self._rng.random() * self.duration_hours * 3600.0
        r = self._rng.random() * self._total
        idx = bisect_left(self._cdf, r)
        idx = min(idx, len(self._xs) - 1)
        # ``_xs`` is in *hours*; convert to seconds as the public API promises.
        return self._xs[idx] * 3600.0

    # ------------------------------------------------------------------ #
    def hourly_histogram(self) -> List[int]:
        """
        Bins the sampled offsets into per-hour counts.

        Useful for tests and the dashboard timeline.
        """
        offsets = self.sample_offsets()
        n_bins = int(self.duration_hours) + 1
        hist = [0] * n_bins
        for off in offsets:
            hour = int(off // 3600) % n_bins
            hist[hour] += 1
        return hist