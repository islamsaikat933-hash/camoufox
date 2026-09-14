"""
Camoufox proxy rotation package.

Two rotation modes are provided, both giving every browser session a distinct
exit IP:

* :class:`~camoufox.proxy.gateway.RotatingGateway` — rotate via a single
  rotating-gateway endpoint (one sticky session = one IP).
* :class:`~camoufox.proxy.textfile.TextFileProxy` — rotate round-robin through
  a plain-text list of proxies, one per line.

Request both from the entry points through the ``proxy_source=``/``gateway=`` /
``proxy_file=`` launch options, or build a custom :class:`ProxySource` and
pass it along.
"""
from .base import ProxySource
from .gateway import RotatingGateway
from .manager import ProxyManager, build_manager_from_config
from .parsing import ProxyEntry, parse_proxy
from .textfile import TextFileProxy

__all__ = [
    "ProxySource",
    "RotatingGateway",
    "TextFileProxy",
    "ProxyManager",
    "ProxyEntry",
    "parse_proxy",
    "build_manager_from_config",
]