import os
import unittest
from importlib import resources

import hypothesis
import pytest
import zoneinfo


def _valid_keys():
    """Determine all valid ZoneInfo keys available on the search path.

    A note of caution: This may attempt to open a large number of files.
    """

    valid_zones = set()

    # Start with loading from the tzdata package if it exists: this has a
    # pre-assembled list of zones that only requires opening one file.
    try:
        with resources.open_text("tzdata", "zones") as f:
            for zone in f:
                zone = zone.strip()
                if zone:
                    valid_zones.add(zone)
    except (ImportError, FileNotFoundError):
        pass

    def valid_key(fpath):
        try:
            with open(fpath, "rb") as f:
                return f.read(4) == b"TZif"
        except Exception:  # pragma: nocover
            pass

    for tz_root in zoneinfo.TZPATH:
        if not os.path.exists(tz_root):
            continue

        for root, _, files in os.walk(tz_root):
            for file in files:
                fpath = os.path.join(root, file)

                key = os.path.relpath(fpath, start=tz_root)
                if os.sep != "/":  # pragma: nocover
                    key = key.replace(os.sep, "/")

                if not key or key in valid_zones:
                    continue

                if valid_key(fpath):
                    valid_zones.add(key)

    return sorted(valid_zones)


VALID_KEYS = _valid_keys()
if not VALID_KEYS:
    pytest.skip("No time zone data available", allow_module_level=True)


def valid_keys():
    return hypothesis.strategies.sampled_from(VALID_KEYS)


class ZoneInfoCacheTest(unittest.TestCase):
    klass = zoneinfo.ZoneInfo

    @hypothesis.given(key=valid_keys())
    def test_cache(self, key):
        zi_0 = self.klass(key)
        zi_1 = self.klass(key)

        self.assertIs(zi_0, zi_1)

    @hypothesis.given(key=valid_keys())
    def test_nocache(self, key):
        zi_0 = self.klass.nocache(key)
        zi_1 = self.klass.nocache(key)

        self.assertIsNot(zi_0, zi_1)
