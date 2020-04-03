__all__ = ["ZoneInfo", "set_tzpath", "TZPATH"]

from . import _tzpath
from ._version import __version__
from ._zoneinfo import ZoneInfo

set_tzpath = _tzpath.set_tzpath


def __getattr__(name):
    if name == "TZPATH":
        return _tzpath.TZPATH
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__ + ["__version__"])
