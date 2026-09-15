"""
Unit tests for `camoufox.traffic`.

Covers the pure logic (scheduler arrivals, behaviour model) and the engine's
orchestration using fake sessions — no browser required.
"""
from __future__ import annotations

import asyncio

import pytest

from camoufox.traffic.behavior import BehaviorModel, EntryChannel
from camoufox.traffic.engine import TrafficEngine
from camoufox.traffic.scheduler import TrafficScheduler
from camoufox.traffic.visitor import VisitorSession


# --------------------------------------------------------------------------- #
# TrafficScheduler
# --------------------------------------------------------------------------- #
class TestScheduler:
    def test_offsets_in_range_and_sorted(self):
        s = TrafficScheduler(50, 24, seed=7)
        offsets = s.sample_offsets()
        assert len(offsets) == 50
        assert offsets == sorted(offsets)
        assert all(0 <= x <= 24 * 3600 for x in offsets)

    def test_reproducible_with_seed(self):
        a = TrafficScheduler(10, 24, seed=99).sample_offsets()
        b = TrafficScheduler(10, 24, seed=99).sample_offsets()
        assert a == b

    def test_distribution_shape(self):
        # with a strong daytime-only curve, most visitors land in daytime hours
        curve = [(0, 0.01), (12, 1.0), (24, 0.01)]
        s = TrafficScheduler(500, 24, curve=curve, seed=5)
        hist = s.hourly_histogram()
        day = sum(hist[8:20])
        night = sum(hist[:8]) + sum(hist[20:])
        assert day > night * 2, (day, night)

    def test_short_duration_scales(self):
        s = TrafficScheduler(10, 2, seed=3)
        offsets = s.sample_offsets()
        assert all(0 <= x <= 2 * 3600 for x in offsets)

    def test_invalid_args(self):
        with pytest.raises(ValueError):
            TrafficScheduler(0)
        with pytest.raises(ValueError):
            TrafficScheduler(5, 0)


# --------------------------------------------------------------------------- #
# BehaviorModel
# --------------------------------------------------------------------------- #
class TestBehavior:
    def test_channels_spread(self):
        b = BehaviorModel(seed=1)
        seen = {b.entry_channel_with_direct() for _ in range(300)}
        assert EntryChannel.SEARCH in seen
        assert EntryChannel.DIRECT in seen
        assert EntryChannel.SOCIAL in seen or EntryChannel.REFERRAL in seen

    def test_page_count_bounds(self):
        b = BehaviorModel(seed=2, max_pages=6)
        for _ in range(50):
            n = b.page_count()
            assert 1 <= n <= 6

    def test_dwell_positive(self):
        b = BehaviorModel(seed=3)
        for _ in range(20):
            assert 2.0 <= b.dwell_time() < 180.0

    def test_page_order_unique_path(self):
        links = ["/a", "/b", "/c", "/d"]
        (landing, _), path = BehaviorModel.page_order(
            "https://x.com/home", links, 3
        )
        assert landing == "https://x.com/home"
        assert len(path) == 3
        assert len(set(path)) == len(path)  # no repeats


# --------------------------------------------------------------------------- #
# VisitorSession (simulated)
# --------------------------------------------------------------------------- #
class TestVisitorSession:
    @pytest.mark.asyncio
    async def test_simulated_bounce(self):
        import camoufox.traffic.visitor as V

        async def no_sleep(_):
            return None

        orig = V._sleep
        V._sleep = no_sleep
        try:
            v = VisitorSession(0, landing="https://x.com", behavior=BehaviorModel(bounce_rate=1.0, seed=1))
            res = await v.run()
        finally:
            V._sleep = orig
        assert res.outcome.value == "completed"
        assert res.pages_viewed == 1

    @pytest.mark.asyncio
    async def test_factory_failure_is_recorded(self):
        async def bad_factory(**kwargs):
            raise RuntimeError("no browser")

        v = VisitorSession(
            1,
            landing="https://x.com",
            behavior=BehaviorModel(seed=2),
            session_factory=bad_factory,
        )
        res = await v.run()
        assert res.outcome.value == "failed"
        assert "no browser" in (res.error or "")


# --------------------------------------------------------------------------- #
# TrafficEngine
# --------------------------------------------------------------------------- #
class TestEngine:
    @pytest.mark.asyncio
    async def test_run_completes_all_visitors(self):
        import camoufox.traffic.visitor as V

        async def fast_sleep(_):
            await asyncio.sleep(0)

        orig = V._sleep
        V._sleep = fast_sleep
        try:
            eng = TrafficEngine(
                "https://example.com",
                n_visitors=5,
                duration_hours=0.0001,
                max_concurrent=2,
                seed=11,
            )
            await eng.start()
            st = eng.stats.snapshot()
            assert st["completed"] == 5
            assert st["failed"] == 0
            assert st["active"] == 0
            assert st["total_planned"] == 5
        finally:
            V._sleep = orig

    @pytest.mark.asyncio
    async def test_proxy_source_passed_to_factory(self):
        import camoufox.traffic.visitor as V

        async def fast_sleep(_):
            await asyncio.sleep(0)

        orig = V._sleep
        V._sleep = fast_sleep
        seen = {}

        class FakeSource:
            def __repr__(self):
                return "FakeSource()"

        async def factory(**kwargs):
            seen.update(kwargs)
            return FakeCtx(), FakePg()

        try:
            eng = TrafficEngine(
                "https://example.com",
                n_visitors=2,
                duration_hours=0.0001,
                max_concurrent=2,
                session_factory=factory,
                proxy_source=FakeSource(),
                seed=1,
            )
            await eng.start()
            assert seen.get("proxy_source") is not None
        finally:
            V._sleep = orig

    @pytest.mark.asyncio
    async def test_stop_drains(self):
        import camoufox.traffic.visitor as V

        async def slow_sleep(_):
            await asyncio.sleep(0.01)

        orig = V._sleep
        V._sleep = slow_sleep
        try:
            eng = TrafficEngine(
                "https://example.com",
                n_visitors=4,
                duration_hours=0.0001,
                max_concurrent=1,
                seed=2,
            )
            task = eng.start()
            await asyncio.sleep(0.03)
            await eng.stop()
            st = eng.stats.snapshot()
            assert st["active"] == 0
            assert st["completed"] + st["failed"] + st["cancelled"] <= 4
        finally:
            V._sleep = orig


class FakeCtx:
    async def new_page(self):
        return FakePg()

    async def close(self):
        return None


class FakePg:
    def __init__(self):
        self.mouse = FakeMouse()

    async def goto(self, url, *, referrer=None):
        self.url = url

    async def evaluate(self, expr):
        return 900


class FakeMouse:
    async def wheel(self, dx, dy):
        return None