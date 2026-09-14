"""
Example: rotating proxies with Camoufox.

Two rotation strategies — the rotating gateway and a text-file proxy pool —
demonstrated with the auto-rotating context API.
"""
from camoufox.proxy import RotatingGateway, TextFileProxy
from camoufox.sync_api import Camoufox, NewContext

GATEWAY = "http://user:pass@gateway.example.com:8000"  # your rotating gateway
PROXY_FILE = "proxies.txt"                            # one proxy per line


def via_gateway() -> None:
    gateway = RotatingGateway(GATEWAY)

    with Camoufox(headless=True) as browser:
        # Each NewContext call draws a fresh exit IP from the gateway.
        for i in range(3):
            with NewContext(browser, proxy_source=gateway) as context:
                page = context.new_page()
                page.goto("https://api.ipify.org")
                ip = page.locator("body").inner_text()
                print(f"session {i}: exit IP = {ip}")


def via_text_file() -> None:
    pool = TextFileProxy(PROXY_FILE)

    with Camoufox(headless=True) as browser:
        for i in range(pool.active_count()):
            with NewContext(browser, proxy_source=pool) as context:
                page = context.new_page()
                page.goto("https://api.ipify.org")
                ip = page.locator("body").inner_text()
                print(f"session {i}: exit IP = {ip}")


if __name__ == "__main__":
    print("== Rotating gateway ==")
    via_gateway()
    print("== Text-file pool ==")
    via_text_file()