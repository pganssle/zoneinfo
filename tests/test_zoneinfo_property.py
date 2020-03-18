import os
import pickle
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


class ZoneInfoTest(unittest.TestCase):
    klass = zoneinfo.ZoneInfo

    @hypothesis.given(key=valid_keys())
    def test_str(self, key):
        zi = self.klass(key)
        self.assertEqual(str(zi), key)

    @hypothesis.given(key=valid_keys())
    def test_pickle_unpickle_cache(self, key):
        zi = self.klass(key)
        pkl_str = pickle.dumps(zi)
        zi_rt = pickle.loads(pkl_str)

        self.assertIs(zi, zi_rt)

    @hypothesis.given(key=valid_keys())
    def test_pickle_unpickle_nocache(self, key):
        zi = self.klass.nocache(key)
        pkl_str = pickle.dumps(zi)
        zi_rt = pickle.loads(pkl_str)

        self.assertIsNot(zi, zi_rt)
        self.assertEqual(str(zi), str(zi_rt))

    @hypothesis.given(key=valid_keys())
    def test_pickle_unpickle_cache_multiple_rounds(self, key):
        """Test that pickle/unpickle is idempotent."""
        zi_0 = self.klass(key)
        pkl_str_0 = pickle.dumps(zi_0)
        zi_1 = pickle.loads(pkl_str_0)
        pkl_str_1 = pickle.dumps(zi_1)
        zi_2 = pickle.loads(pkl_str_1)
        pkl_str_2 = pickle.dumps(zi_2)

        self.assertEqual(pkl_str_0, pkl_str_1)
        self.assertEqual(pkl_str_1, pkl_str_2)

        self.assertIs(zi_0, zi_1)
        self.assertIs(zi_0, zi_2)
        self.assertIs(zi_1, zi_2)

    @hypothesis.given(key=valid_keys())
    def test_pickle_unpickle_nocache_multiple_rounds(self, key):
        """Test that pickle/unpickle is idempotent."""
        zi_cache = self.klass(key)

        zi_0 = self.klass.nocache(key)
        pkl_str_0 = pickle.dumps(zi_0)
        zi_1 = pickle.loads(pkl_str_0)
        pkl_str_1 = pickle.dumps(zi_1)
        zi_2 = pickle.loads(pkl_str_1)
        pkl_str_2 = pickle.dumps(zi_2)

        self.assertEqual(pkl_str_0, pkl_str_1)
        self.assertEqual(pkl_str_1, pkl_str_2)

        self.assertIsNot(zi_0, zi_1)
        self.assertIsNot(zi_0, zi_2)
        self.assertIsNot(zi_1, zi_2)

        self.assertIsNot(zi_0, zi_cache)
        self.assertIsNot(zi_1, zi_cache)
        self.assertIsNot(zi_2, zi_cache)


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
