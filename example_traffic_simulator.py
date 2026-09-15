"""
Camoufox Real-Human Traffic Simulator — example usage.

Two modes:

* ``python example_traffic_simulator.py --gui``
    Opens the browser-based dashboard where you choose the number of visitors,
    the 24-hour window and proxy rotation settings.

* ``python example_traffic_simulator.py``
    Runs a headless simulation directly with the settings below.

Settings are the same ones you see in the GUI. Proxy rotation accepts exactly
one of ``--proxy``, ``--proxy-file``, ``--gateway`` (same options as the
installed ``camoufox.proxy`` package).
"""
from __future__ import annotations

import argparse


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gui", action="store_true", help="open the dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--open", action="store_true", help="auto-open browser tab")
    # run settings
    p.add_argument("--landing", default="https://httpbin.org/anything", help="target URL")
    p.add_argument("--visitors", type=int, default=100)
    p.add_argument("--hours", type=float, default=24.0, help="window in hours")
    p.add_argument("--concurrent", type=int, default=8)
    p.add_argument("--seed", type=int, default=None)
    # proxy rotation — use exactly one
    p.add_argument("--proxy", help="static proxy URL")
    p.add_argument("--proxy-file", help="text file with one proxy per line")
    p.add_argument("--gateway", help="rotating gateway endpoint")
    args = p.parse_args(argv)

    if args.gui:
        from camoufox.traffic.server import serve_dashboard

        serve_dashboard(host=args.host, port=args.port, open_browser=args.open)
        return 0

    import asyncio

    from camoufox.traffic.engine import build_engine_from_config

    engine = build_engine_from_config(
        landing=args.landing,
        visitors=args.visitors,
        duration_hours=args.hours,
        max_concurrent=args.concurrent,
        proxy=args.proxy,
        proxy_file=args.proxy_file,
        gateway=args.gateway,
        seed=args.seed,
        on_stats=lambda s: print_stats(s),
    )

    async def _run():
        print(f"Simulating {args.visitors} human visitors over {args.hours} h…")
        await engine.start()

    asyncio.run(_run())
    st = engine.stats.snapshot()
    print("\nDone.")
    print(f"  completed : {st['completed']}")
    print(f"  failed    : {st['failed']}")
    print(f"  pages     : {st['total_pages']}")
    print(f"  channels  : {st['channels']}")
    return 0


def print_stats(stats: dict) -> None:
    done = stats.get("progress", 0)
    total = stats["total_planned"]
    act = stats.get("active", 0)
    print(
        f"\r  launched {stats.get('launched', 0):>4}/{total:<4}  "
        f"done {done:<4}  active {act}  pages {stats.get('total_pages', 0)}",
        end="",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())