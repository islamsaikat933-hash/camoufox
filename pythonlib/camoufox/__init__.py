from .addons import DefaultAddons
from .async_api import AsyncCamoufox, AsyncNewBrowser, AsyncNewContext
from .proxy import ProxySource, RotatingGateway, TextFileProxy
from .sync_api import Camoufox, NewBrowser, NewContext
from .utils import launch_options

__all__ = [
    "Camoufox",
    "NewBrowser",
    "NewContext",
    "AsyncCamoufox",
    "AsyncNewBrowser",
    "AsyncNewContext",
    "DefaultAddons",
    "ProxySource",
    "RotatingGateway",
    "TextFileProxy",
    "launch_options",
]
