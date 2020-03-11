__all__ = ["ZoneInfo", "set_tzpath", "TZPATH"]

from ._tzpath import set_tzpath
from ._version import __version__
from ._zoneinfo import ZoneInfo


def __getattr__(name):
    if name == "TZPATH":
        from . import _tzpath

        return _tzpath.TZPATH
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__ + ["__version__"])
