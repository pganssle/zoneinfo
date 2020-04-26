import os
import sys


def reset_tzpath(to=None):
    global TZPATH

    tzpaths = to
    if tzpaths is not None:
        if isinstance(tzpaths, (str, bytes)):
            raise TypeError(
                f"tzpaths must be a list or tuple, "
                + f"not {type(tzpaths)}: {tzpaths}"
            )
        base_tzpath = tzpaths
    else:
        if "PYTHONTZPATH" in os.environ:
            env_var = os.environ["PYTHONTZPATH"]
            if env_var:
                base_tzpath = env_var.split(os.pathsep)
            else:
                base_tzpath = ()
        elif sys.platform != "win32":
            base_tzpath = [
                "/usr/share/zoneinfo",
                "/usr/lib/zoneinfo",
                "/usr/share/lib/zoneinfo",
                "/etc/zoneinfo",
            ]

            base_tzpath.sort(key=lambda x: not os.path.exists(x))
        else:
            base_tzpath = ()

    TZPATH = tuple(base_tzpath)


def find_tzfile(key):
    """Retrieve the path to a TZif file from a key."""
    _validate_path(key)
    for search_path in TZPATH:
        filepath = os.path.join(search_path, key)
        if os.path.isfile(filepath):
            return filepath

    return None


_TEST_PATH = os.path.normpath(os.path.join("_", "_"))[:-1]


def _validate_path(path, _base=_TEST_PATH):
    if os.path.isabs(path):
        raise ValueError(
            f"ZoneInfo keys may not be absolute paths, got: {path}"
        )

    # We only care about the kinds of path normalizations that would change the
    # length of the key - e.g. a/../b -> a/b, or a/b/ -> a/b. On Windows,
    # normpath will also change from a/b to a\b, but that would still preserve
    # the length.
    new_path = os.path.normpath(path)
    if len(new_path) != len(path):
        raise ValueError(
            f"ZoneInfo keys must be normalized relative paths, got: {path}"
        )

    resolved = os.path.normpath(os.path.join(_base, new_path))
    if not resolved.startswith(_base):
        raise ValueError(
            f"ZoneInfo keys must refer to subdirectories of TZPATH, got: {path}"
        )


del _TEST_PATH


TZPATH = ()
reset_tzpath()
