from __future__ import annotations

import base64
import contextlib
import dataclasses
import importlib.metadata
import io
import json
import lzma
import os
import pathlib
import pickle
import re
import shutil
import struct
import tempfile
import threading
import unittest
from datetime import datetime, time, timedelta, timezone

import zoneinfo
from zoneinfo import ZoneInfo

try:
    importlib.metadata.metadata("tzdata")
    HAS_TZDATA_PKG = True
except importlib.metadata.PackageNotFoundError:
    HAS_TZDATA_PKG = False

OS_ENV_LOCK = threading.Lock()
TZPATH_LOCK = threading.Lock()
TZPATH_TEST_LOCK = threading.Lock()

ZONEINFO_DATA = None
ZONEINFO_DATA_V1 = None
TEMP_DIR = None
DATA_DIR = pathlib.Path(__file__).parent / "data"
ZONEINFO_JSON = DATA_DIR / "zoneinfo_data.json"

# Useful constants
ZERO = timedelta(0)
ONE_H = timedelta(hours=1)


def setUpModule():
    global TEMP_DIR
    global ZONEINFO_DATA
    global ZONEINFO_DATA_V1

    TEMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="zoneinfo"))
    ZONEINFO_DATA = ZoneInfoData(ZONEINFO_JSON, TEMP_DIR / "v2")
    ZONEINFO_DATA_V1 = ZoneInfoData(ZONEINFO_JSON, TEMP_DIR / "v1", v1=True)


def tearDownModule():
    shutil.rmtree(TEMP_DIR)


@contextlib.contextmanager
def tzpath_context(tzpath, lock=TZPATH_LOCK):
    with lock:
        old_path = zoneinfo.TZPATH
        try:
            zoneinfo.set_tzpath(tzpath)
            yield
        finally:
            zoneinfo.set_tzpath(old_path)


class TzPathUserMixin:
    """
    Adds a setUp() and tearDown() to make TZPATH manipulations thread-safe.

    Any tests that require manipulation of the TZPATH global are necessarily
    thread unsafe, so we will acquire a lock and reset the TZPATH variable
    to the default state before each test and release the lock after the test
    is through.
    """

    @property
    def tzpath(self):  # pragma: nocover
        return None

    def setUp(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                tzpath_context(self.tzpath, lock=TZPATH_TEST_LOCK)
            )
            self.addCleanup(stack.pop_all().close)

        super().setUp()


class ZoneInfoTest(TzPathUserMixin, unittest.TestCase):
    @property
    def tzpath(self):
        return [ZONEINFO_DATA.tzpath]

    def zone_from_key(self, key):
        return ZoneInfo(key)

    def zones(self):
        return ZoneDumpData.transition_keys()

    def load_transition_examples(self, key):
        return ZoneDumpData.load_transition_examples(key)

    def test_str(self):
        # Zones constructed with a key must have str(zone) == key
        for key in self.zones():
            with self.subTest(key):
                zi = self.zone_from_key(key)

                self.assertEqual(str(zi), key)

        # Zones with no key constructed should have str(zone) == repr(zone)
        file_key = ZONEINFO_DATA.keys[0]
        file_path = ZONEINFO_DATA.path_from_key(file_key)

        with open(file_path, "rb") as f:
            with self.subTest(test_name="Repr test", path=file_path):
                zi_ff = ZoneInfo.from_file(f)
                self.assertEqual(str(zi_ff), repr(zi_ff))

    def test_repr(self):
        # The repr is not guaranteed, but I think we can insist that it at
        # least contain the name of the class.
        key = next(iter(self.zones()))

        zi = ZoneInfo(key)
        class_name = "ZoneInfo"
        with self.subTest(name="from key"):
            self.assertRegex(repr(zi), class_name)

        file_key = ZONEINFO_DATA.keys[0]
        file_path = ZONEINFO_DATA.path_from_key(file_key)
        with open(file_path, "rb") as f:
            zi_ff = ZoneInfo.from_file(f, key=file_key)

        with self.subTest(name="from file with key"):
            self.assertRegex(repr(zi_ff), class_name)

        with open(file_path, "rb") as f:
            zi_ff_nk = ZoneInfo.from_file(f)

        with self.subTest(name="from file without key"):
            self.assertRegex(repr(zi_ff_nk), class_name)

    def test_bad_zones(self):
        bad_zones = [
            b"",  # Empty file
            b"AAAA3" + b" " * 15,  # Bad magic
        ]

        for bad_zone in bad_zones:
            fobj = io.BytesIO(bad_zone)
            with self.assertRaises(ValueError):
                ZoneInfo.from_file(fobj)

    def test_unambiguous(self):
        test_cases = []
        for key in self.zones():
            for zone_transition in self.load_transition_examples(key):
                test_cases.append(
                    (
                        key,
                        zone_transition.transition - timedelta(days=2),
                        zone_transition.offset_before,
                    )
                )

                test_cases.append(
                    (
                        key,
                        zone_transition.transition + timedelta(days=2),
                        zone_transition.offset_after,
                    )
                )

        for key, dt, offset in test_cases:
            with self.subTest(key=key, dt=dt, offset=offset):
                tzi = self.zone_from_key(key)
                dt = dt.replace(tzinfo=tzi)

                self.assertEqual(dt.tzname(), offset.tzname, dt)
                self.assertEqual(dt.utcoffset(), offset.utcoffset, dt)
                self.assertEqual(dt.dst(), offset.dst, dt)

    def test_folds_and_gaps(self):
        test_cases = []
        for key in self.zones():
            tests = {"folds": [], "gaps": []}
            for zt in self.load_transition_examples(key):
                if zt.fold:
                    test_group = tests["folds"]
                elif zt.gap:
                    test_group = tests["gaps"]
                else:
                    # Assign a random variable here to disable the peephole
                    # optimizer so that coverage can see this line.
                    # See bpo-2506 for more information.
                    no_peephole_opt = None
                    continue

                # Cases are of the form key, dt, fold, offset
                dt = zt.anomaly_start - timedelta(seconds=1)
                test_group.append((dt, 0, zt.offset_before))
                test_group.append((dt, 1, zt.offset_before))

                dt = zt.anomaly_start
                test_group.append((dt, 0, zt.offset_before))
                test_group.append((dt, 1, zt.offset_after))

                dt = zt.anomaly_start + timedelta(seconds=1)
                test_group.append((dt, 0, zt.offset_before))
                test_group.append((dt, 1, zt.offset_after))

                dt = zt.anomaly_end - timedelta(seconds=1)
                test_group.append((dt, 0, zt.offset_before))
                test_group.append((dt, 1, zt.offset_after))

                dt = zt.anomaly_end
                test_group.append((dt, 0, zt.offset_after))
                test_group.append((dt, 1, zt.offset_after))

                dt = zt.anomaly_end + timedelta(seconds=1)
                test_group.append((dt, 0, zt.offset_after))
                test_group.append((dt, 1, zt.offset_after))

            for grp, test_group in tests.items():
                test_cases.append(((key, grp), test_group))

        for (key, grp), tests in test_cases:
            with self.subTest(key=key, grp=grp):
                tzi = self.zone_from_key(key)

                for dt, fold, offset in tests:
                    dt = dt.replace(fold=fold, tzinfo=tzi)

                    self.assertEqual(dt.tzname(), offset.tzname, dt)
                    self.assertEqual(dt.utcoffset(), offset.utcoffset, dt)
                    self.assertEqual(dt.dst(), offset.dst, dt)

    def test_folds_from_utc(self):
        tests = []
        for key in self.zones():
            zi = self.zone_from_key(key)
            with self.subTest(key=key):
                for zt in self.load_transition_examples(key):
                    if not zt.fold:
                        continue

                    dt_utc = zt.transition_utc
                    dt_before_utc = dt_utc - timedelta(seconds=1)
                    dt_after_utc = dt_utc + timedelta(seconds=1)

                    dt_before = dt_before_utc.astimezone(zi)
                    self.assertEqual(dt_before.fold, 0, (dt_before, dt_utc))

                    dt_after = dt_after_utc.astimezone(zi)
                    self.assertEqual(dt_after.fold, 1, (dt_after, dt_utc))


class ZoneInfoV1Test(ZoneInfoTest):
    @property
    def tzpath(self):
        return [ZONEINFO_DATA_V1.tzpath]

    def load_transition_examples(self, key):
        # We will discard zdump examples outside the range epoch +/- 2**31,
        # because they are not well-supported in Version 1 files.
        epoch = datetime(1970, 1, 1)
        max_offset_32 = timedelta(seconds=2 ** 31)
        min_dt = epoch - max_offset_32
        max_dt = epoch + max_offset_32

        for zt in ZoneDumpData.load_transition_examples(key):
            if min_dt <= zt.transition <= max_dt:
                yield zt


@unittest.skipIf(
    not HAS_TZDATA_PKG, "Skipping tzdata-specific tests: tzdata not installed"
)
class TZDataTests(ZoneInfoTest):
    """
    Runs all the ZoneInfoTest tests, but against the tzdata package

    NOTE: The ZoneDumpData has frozen test data, but tzdata will update, so
    some of the tests (particularly those related to the far future) may break
    in the event that the time zone policies in the relevant time zones change.
    """

    @property
    def tzpath(self):
        return []

    def zone_from_key(self, key):
        return ZoneInfo(key=key)


class TZStrTest(unittest.TestCase):
    NORMAL = 0
    FOLD = 1
    GAP = 2

    @classmethod
    def setUpClass(cls):
        cls._populate_test_cases()

    def _zone_from_tzstr(self, tzstr):
        """Creates a zoneinfo file following a POSIX rule."""
        zonefile = io.BytesIO()
        # Version 1 header
        zonefile.write(b"TZif")  # Magic value
        zonefile.write(b"3")  # Version
        zonefile.write(b" " * 15)  # Reserved
        # We will not write any of the manual transition parts
        zonefile.write(struct.pack(">6l", 0, 0, 0, 0, 0, 0))

        # Version 2+ header
        zonefile.write(b"TZif")  # Magic value
        zonefile.write(b"3")  # Version
        zonefile.write(b" " * 15)  # Reserved
        zonefile.write(struct.pack(">6l", 0, 0, 0, 1, 1, 4))

        # Add an arbitrary offset to make things easier
        zonefile.write(struct.pack(">1q", -(2 ** 32)))
        zonefile.write(struct.pack(">1B", 0))
        zonefile.write(struct.pack(">lbb", -17760, 0, 0))
        zonefile.write(b"LMT\x00")

        # Write the footer
        zonefile.write(b"\x0A")
        zonefile.write(tzstr.encode("ascii"))
        zonefile.write(b"\x0A")

        zonefile.seek(0)

        return ZoneInfo.from_file(zonefile, key=tzstr)

    def test_tzstr_localized(self):
        i = 0
        for tzstr, cases in self.test_cases.items():
            with self.subTest(tzstr=tzstr):
                zi = self._zone_from_tzstr(tzstr)

            for dt_naive, offset, _ in cases:
                dt = dt_naive.replace(tzinfo=zi)

                with self.subTest(tzstr=tzstr, dt=dt, offset=offset):
                    self.assertEqual(dt.tzname(), offset.tzname)
                    self.assertEqual(dt.utcoffset(), offset.utcoffset)
                    self.assertEqual(dt.dst(), offset.dst)

    def test_tzstr_from_utc(self):
        for tzstr, cases in self.test_cases.items():
            with self.subTest(tzstr=tzstr):
                zi = self._zone_from_tzstr(tzstr)

            for dt_naive, offset, dt_type in cases:
                if dt_type == self.GAP:
                    continue  # Cannot create a gap from UTC

                dt_utc = (dt_naive - offset.utcoffset).replace(
                    tzinfo=timezone.utc
                )

                # Check that we can go UTC -> Our zone
                dt_act = dt_utc.astimezone(zi)
                dt_exp = dt_naive.replace(tzinfo=zi)

                self.assertEqual(dt_act, dt_exp)

                if dt_type == self.FOLD:
                    self.assertEqual(dt_act.fold, dt_naive.fold, dt_naive)
                else:
                    self.assertEqual(dt_act.fold, 0)

                # Now check that we can go our zone -> UTC
                dt_act = dt_exp.astimezone(timezone.utc)

                self.assertEqual(dt_act, dt_utc)

    def test_invalid_tzstr(self):
        invalid_tzstrs = [
            "PST8PDT",  # DST but no transition specified
            "+11",  # Unquoted alphanumeric
            "GMT,M3.2.0/2,M11.1.0/3",  # Transition rule but no DST
            # Invalid offsets
            "STD+25",
            "STD-25",
            "STD+374",
            "STD+374DST,M3.2.0/2,M11.1.0/3",
            "STD+23DST+25,M3.2.0/2,M11.1.0/3",
            "STD-23DST-25,M3.2.0/2,M11.1.0/3",
            # Completely invalid dates
            "AAA4BBB,M1443339,M11.1.0/3",
            "AAA4BBB,M3.2.0/2,0349309483959c",
            # Invalid months
            "AAA4BBB,M13.1.1/2,M1.1.1/2",
            "AAA4BBB,M1.1.1/2,M13.1.1/2",
            "AAA4BBB,M0.1.1/2,M1.1.1/2",
            "AAA4BBB,M1.1.1/2,M0.1.1/2",
            # Invalid weeks
            "AAA4BBB,M1.6.1/2,M1.1.1/2",
            "AAA4BBB,M1.1.1/2,M1.6.1/2",
            # Invalid weekday
            "AAA4BBB,M1.1.7/2,M2.1.1/2",
            "AAA4BBB,M1.1.1/2,M2.1.7/2",
            # Invalid numeric offset
            "AAA4BBB,-1/2,20/2",
            "AAA4BBB,1/2,-1/2",
            "AAA4BBB,367,20/2",
            "AAA4BBB,1/2,367/2",
            # Invalid julian offset
            "AAA4BBB,J0/2,J20/2",
            "AAA4BBB,J20/2,J366/2",
        ]

        for invalid_tzstr in invalid_tzstrs:
            with self.subTest(tzstr=invalid_tzstr):
                # Not necessarily a guaranteed property, but we should show
                # the problematic TZ string if that's the cause of failure.
                tzstr_regex = re.escape(invalid_tzstr)
                with self.assertRaisesRegex(ValueError, tzstr_regex):
                    self._zone_from_tzstr(invalid_tzstr)

    @classmethod
    def _populate_test_cases(cls):
        # This method uses a somewhat unusual style in that it populates the
        # test cases for each tzstr by using a decorator to automatically call
        # a function that mutates the current dictionary of test cases.
        #
        # The population of the test cases is done in individual functions to
        # give each set of test cases its own namespace in which to define
        # its offsets (this way we don't have to worry about variable reuse
        # causing problems if someone makes a typo).
        #
        # The decorator for calling is used to make it more obvious that each
        # function is actually called (if it's not decorated, it's not called).
        def call(f):
            """Decorator to call the addition methods.

            This will call a function which adds at least one new entry into
            the `cases` dictionary. The decorator will also assert that
            something was added to the dictionary.
            """
            prev_len = len(cases)
            f()
            assert len(cases) > prev_len, "Function did not add a test case!"

        NORMAL = cls.NORMAL
        FOLD = cls.FOLD
        GAP = cls.GAP

        cases = {}

        @call
        def _add():
            # Transition to EDT on the 2nd Sunday in March at 4 AM, and
            # transition back on the first Sunday in November at 3AM
            tzstr = "EST5EDT,M3.2.0/4:00,M11.1.0/3:00"

            EST = ZoneOffset("EST", timedelta(hours=-5), ZERO)
            EDT = ZoneOffset("EDT", timedelta(hours=-4), ONE_H)

            cases[tzstr] = (
                (datetime(2019, 3, 9), EST, NORMAL),
                (datetime(2019, 3, 10, 3, 59), EST, NORMAL),
                (datetime(2019, 3, 10, 4, 0, fold=0), EST, GAP),
                (datetime(2019, 3, 10, 4, 0, fold=1), EDT, GAP),
                (datetime(2019, 3, 10, 4, 1, fold=0), EST, GAP),
                (datetime(2019, 3, 10, 4, 1, fold=1), EDT, GAP),
                (datetime(2019, 11, 2), EDT, NORMAL),
                (datetime(2019, 11, 3, 1, 59, fold=1), EDT, NORMAL),
                (datetime(2019, 11, 3, 2, 0, fold=0), EDT, FOLD),
                (datetime(2019, 11, 3, 2, 0, fold=1), EST, FOLD),
                (datetime(2020, 3, 8, 3, 59), EST, NORMAL),
                (datetime(2020, 3, 8, 4, 0, fold=0), EST, GAP),
                (datetime(2020, 3, 8, 4, 0, fold=1), EDT, GAP),
                (datetime(2020, 11, 1, 1, 59, fold=1), EDT, NORMAL),
                (datetime(2020, 11, 1, 2, 0, fold=0), EDT, FOLD),
                (datetime(2020, 11, 1, 2, 0, fold=1), EST, FOLD),
            )

        @call
        def _add():
            # Transition to BST happens on the last Sunday in March at 1 AM GMT
            # and the transition back happens the last Sunday in October at 2AM BST
            tzstr = "GMT0BST-1,M3.5.0/1:00,M10.5.0/2:00"

            GMT = ZoneOffset("GMT", ZERO, ZERO)
            BST = ZoneOffset("BST", ONE_H, ONE_H)

            cases[tzstr] = (
                (datetime(2019, 3, 30), GMT, NORMAL),
                (datetime(2019, 3, 31, 0, 59), GMT, NORMAL),
                (datetime(2019, 3, 31, 2, 0), BST, NORMAL),
                (datetime(2019, 10, 26), BST, NORMAL),
                (datetime(2019, 10, 27, 0, 59, fold=1), BST, NORMAL),
                (datetime(2019, 10, 27, 1, 0, fold=0), BST, GAP),
                (datetime(2019, 10, 27, 2, 0, fold=1), GMT, GAP),
                (datetime(2020, 3, 29, 0, 59), GMT, NORMAL),
                (datetime(2020, 3, 29, 2, 0), BST, NORMAL),
                (datetime(2020, 10, 25, 0, 59, fold=1), BST, NORMAL),
                (datetime(2020, 10, 25, 1, 0, fold=0), BST, FOLD),
                (datetime(2020, 10, 25, 2, 0, fold=1), GMT, NORMAL),
            )

        @call
        def _add():
            # Austrialian time zone - DST start is chronologically first
            tzstr = "AEST-10AEDT,M10.1.0/2,M4.1.0/3"

            AEST = ZoneOffset("AEST", timedelta(hours=10), ZERO)
            AEDT = ZoneOffset("AEDT", timedelta(hours=11), ONE_H)

            cases[tzstr] = (
                (datetime(2019, 4, 6), AEDT, NORMAL),
                (datetime(2019, 4, 7, 1, 59), AEDT, NORMAL),
                (datetime(2019, 4, 7, 1, 59, fold=1), AEDT, NORMAL),
                (datetime(2019, 4, 7, 2, 0, fold=0), AEDT, FOLD),
                (datetime(2019, 4, 7, 2, 1, fold=0), AEDT, FOLD),
                (datetime(2019, 4, 7, 2, 0, fold=1), AEST, FOLD),
                (datetime(2019, 4, 7, 2, 1, fold=1), AEST, FOLD),
                (datetime(2019, 4, 7, 3, 0, fold=0), AEST, NORMAL),
                (datetime(2019, 4, 7, 3, 0, fold=1), AEST, NORMAL),
                (datetime(2019, 10, 5, 0), AEST, NORMAL),
                (datetime(2019, 10, 6, 1, 59), AEST, NORMAL),
                (datetime(2019, 10, 6, 2, 0, fold=0), AEST, GAP),
                (datetime(2019, 10, 6, 2, 0, fold=1), AEDT, GAP),
                (datetime(2019, 10, 6, 3, 0), AEDT, NORMAL),
            )

        @call
        def _add():
            # Irish time zone - negative DST
            tzstr = "IST-1GMT0,M10.5.0,M3.5.0/1"

            GMT = ZoneOffset("GMT", ZERO, -ONE_H)
            IST = ZoneOffset("IST", ONE_H, ZERO)

            cases[tzstr] = (
                (datetime(2019, 3, 30), GMT, NORMAL),
                (datetime(2019, 3, 31, 0, 59), GMT, NORMAL),
                (datetime(2019, 3, 31, 2, 0), IST, NORMAL),
                (datetime(2019, 10, 26), IST, NORMAL),
                (datetime(2019, 10, 27, 0, 59, fold=1), IST, NORMAL),
                (datetime(2019, 10, 27, 1, 0, fold=0), IST, FOLD),
                (datetime(2019, 10, 27, 1, 0, fold=1), GMT, FOLD),
                (datetime(2019, 10, 27, 2, 0, fold=1), GMT, NORMAL),
                (datetime(2020, 3, 29, 0, 59), GMT, NORMAL),
                (datetime(2020, 3, 29, 2, 0), IST, NORMAL),
                (datetime(2020, 10, 25, 0, 59, fold=1), IST, NORMAL),
                (datetime(2020, 10, 25, 1, 0, fold=0), IST, FOLD),
                (datetime(2020, 10, 25, 2, 0, fold=1), GMT, NORMAL),
            )

        @call
        def _add():
            # Pacific/Kosrae: Fixed offset zone with a quoted numerical tzname
            tzstr = "<+11>-11"

            cases[tzstr] = (
                (
                    datetime(2020, 1, 1),
                    ZoneOffset("+11", timedelta(hours=11)),
                    NORMAL,
                ),
            )

        @call
        def _add():
            # Quoted STD and DST, transitions at 24:00
            tzstr = "<-04>4<-03>,M9.1.6/24,M4.1.6/24"

            M04 = ZoneOffset("-04", timedelta(hours=-4))
            M03 = ZoneOffset("-03", timedelta(hours=-3), ONE_H)

            cases[tzstr] = (
                (datetime(2020, 5, 1), M04, NORMAL),
                (datetime(2020, 11, 1), M03, NORMAL),
            )

        @call
        def _add():
            # Permanent daylight saving time is modeled with transitions at 0/0
            # and J365/25, as mentioned in RFC 8536 Section 3.3.1
            tzstr = "EST5EDT,0/0,J365/25"

            EDT = ZoneOffset("EDT", timedelta(hours=-4), ONE_H)

            cases[tzstr] = (
                (datetime(2019, 1, 1), EDT, NORMAL),
                (datetime(2019, 6, 1), EDT, NORMAL),
                (datetime(2019, 12, 31, 23, 59, 59, 999999), EDT, NORMAL),
                (datetime(2020, 1, 1), EDT, NORMAL),
                (datetime(2020, 3, 1), EDT, NORMAL),
                (datetime(2020, 6, 1), EDT, NORMAL),
                (datetime(2020, 12, 31, 23, 59, 59, 999999), EDT, NORMAL),
                (datetime(2400, 1, 1), EDT, NORMAL),
                (datetime(2400, 3, 1), EDT, NORMAL),
                (datetime(2400, 12, 31, 23, 59, 59, 999999), EDT, NORMAL),
            )

        @call
        def _add():
            # Transitions on March 1st and November 1st of each year
            tzstr = "AAA3BBB,J60/12,J305/12"

            AAA = ZoneOffset("AAA", timedelta(hours=-3))
            BBB = ZoneOffset("BBB", timedelta(hours=-2), ONE_H)

            cases[tzstr] = (
                (datetime(2019, 1, 1), AAA, NORMAL),
                (datetime(2019, 2, 28), AAA, NORMAL),
                (datetime(2019, 3, 1, 11, 59), AAA, NORMAL),
                (datetime(2019, 3, 1, 12, fold=0), AAA, GAP),
                (datetime(2019, 3, 1, 12, fold=1), BBB, GAP),
                (datetime(2019, 3, 1, 13), BBB, NORMAL),
                (datetime(2019, 11, 1, 10, 59), BBB, NORMAL),
                (datetime(2019, 11, 1, 11, fold=0), BBB, FOLD),
                (datetime(2019, 11, 1, 11, fold=1), AAA, FOLD),
                (datetime(2019, 11, 1, 12), AAA, NORMAL),
                (datetime(2019, 12, 31, 23, 59, 59, 999999), AAA, NORMAL),
                (datetime(2020, 1, 1), AAA, NORMAL),
                (datetime(2020, 2, 29), AAA, NORMAL),
                (datetime(2020, 3, 1, 11, 59), AAA, NORMAL),
                (datetime(2020, 3, 1, 12, fold=0), AAA, GAP),
                (datetime(2020, 3, 1, 12, fold=1), BBB, GAP),
                (datetime(2020, 3, 1, 13), BBB, NORMAL),
                (datetime(2020, 11, 1, 10, 59), BBB, NORMAL),
                (datetime(2020, 11, 1, 11, fold=0), BBB, FOLD),
                (datetime(2020, 11, 1, 11, fold=1), AAA, FOLD),
                (datetime(2020, 11, 1, 12), AAA, NORMAL),
                (datetime(2020, 12, 31, 23, 59, 59, 999999), AAA, NORMAL),
            )

        cls.test_cases = cases


class ZoneInfoCacheTest(TzPathUserMixin, unittest.TestCase):
    def setUp(self):
        ZoneInfo.clear_cache()
        super().setUp()

    @property
    def tzpath(self):
        return [ZONEINFO_DATA.tzpath]

    def test_ephemeral_zones(self):
        self.assertIs(
            ZoneInfo("America/Los_Angeles"), ZoneInfo("America/Los_Angeles")
        )

    def test_strong_refs(self):
        tz0 = ZoneInfo("Australia/Sydney")
        tz1 = ZoneInfo("Australia/Sydney")

        self.assertIs(tz0, tz1)

    def test_nocache(self):

        tz0 = ZoneInfo("Europe/Lisbon")
        tz1 = ZoneInfo.nocache("Europe/Lisbon")

        self.assertIsNot(tz0, tz1)

    def test_cache_set_tzpath(self):
        """Test that the cache persists when tzpath has been changed.

        The PEP specifies that as long as a reference exists to one zone
        with a given key, the primary constructor must continue to return
        the same object.
        """
        zi0 = ZoneInfo("America/Los_Angeles")
        with tzpath_context([]):
            zi1 = ZoneInfo("America/Los_Angeles")

        self.assertIs(zi0, zi1)

    def test_clear_cache_one_key(self):
        """Tests that you can clear a single key from the cache."""
        la0 = ZoneInfo("America/Los_Angeles")
        dub0 = ZoneInfo("Europe/Dublin")

        ZoneInfo.clear_cache(only_keys=["America/Los_Angeles"])

        la1 = ZoneInfo("America/Los_Angeles")
        dub1 = ZoneInfo("Europe/Dublin")

        self.assertIsNot(la0, la1)
        self.assertIs(dub0, dub1)

    def test_clear_cache_two_keys(self):
        la0 = ZoneInfo("America/Los_Angeles")
        dub0 = ZoneInfo("Europe/Dublin")
        tok0 = ZoneInfo("Asia/Tokyo")

        ZoneInfo.clear_cache(only_keys=["America/Los_Angeles", "Europe/Dublin"])

        la1 = ZoneInfo("America/Los_Angeles")
        dub1 = ZoneInfo("Europe/Dublin")
        tok1 = ZoneInfo("Asia/Tokyo")

        self.assertIsNot(la0, la1)
        self.assertIsNot(dub0, dub1)
        self.assertIs(tok0, tok1)


class ZoneInfoPickleTest(TzPathUserMixin, unittest.TestCase):
    def setUp(self):
        ZoneInfo.clear_cache()
        super().setUp()

    @property
    def tzpath(self):
        return [ZONEINFO_DATA.tzpath]

    def test_cache_hit(self):
        zi_in = ZoneInfo("Europe/Dublin")
        pkl = pickle.dumps(zi_in)
        zi_rt = pickle.loads(pkl)

        with self.subTest(test="Is non-pickled ZoneInfo"):
            self.assertIs(zi_in, zi_rt)

        zi_rt2 = pickle.loads(pkl)
        with self.subTest(test="Is unpickled ZoneInfo"):
            self.assertIs(zi_rt, zi_rt2)

    def test_cache_miss(self):
        zi_in = ZoneInfo("Europe/Dublin")
        pkl = pickle.dumps(zi_in)

        del zi_in
        ZoneInfo.clear_cache()  # Induce a cache miss
        zi_rt = pickle.loads(pkl)
        zi_rt2 = pickle.loads(pkl)

        self.assertIs(zi_rt, zi_rt2)

    def test_nocache(self):
        zi_nocache = ZoneInfo.nocache("Europe/Dublin")

        pkl = pickle.dumps(zi_nocache)
        zi_rt = pickle.loads(pkl)

        with self.subTest(test="Not the pickled object"):
            self.assertIsNot(zi_rt, zi_nocache)

        zi_rt2 = pickle.loads(pkl)
        with self.subTest(test="Not a second unpickled object"):
            self.assertIsNot(zi_rt, zi_rt2)

        zi_cache = ZoneInfo("Europe/Dublin")
        with self.subTest(test="Not a cached object"):
            self.assertIsNot(zi_rt, zi_cache)

    def test_from_file(self):
        key = "Europe/Dublin"
        with open(ZONEINFO_DATA.path_from_key(key), "rb") as f:
            zi_nokey = ZoneInfo.from_file(f)

            f.seek(0)
            zi_key = ZoneInfo.from_file(f, key=key)

        test_cases = [
            (zi_key, "ZoneInfo with key"),
            (zi_nokey, "ZoneInfo without key"),
        ]

        for zi, test_name in test_cases:
            with self.subTest(test_name=test_name):
                with self.assertRaises(pickle.PicklingError):
                    pickle.dumps(zi)

    def test_pickle_after_from_file(self):
        # This may be a bit of paranoia, but this test is to ensure that no
        # global state is maintained in order to handle the pickle cache and
        # from_file behavior, and that it is possible to interweave the
        # constructors of each of these and pickling/unpickling without issues.
        key = "Europe/Dublin"
        zi = ZoneInfo(key)

        pkl_0 = pickle.dumps(zi)
        zi_rt_0 = pickle.loads(pkl_0)
        self.assertIs(zi, zi_rt_0)

        with open(ZONEINFO_DATA.path_from_key(key), "rb") as f:
            zi_ff = ZoneInfo.from_file(f, key=key)

        pkl_1 = pickle.dumps(zi)
        zi_rt_1 = pickle.loads(pkl_1)
        self.assertIs(zi, zi_rt_1)

        with self.assertRaises(pickle.PicklingError):
            pickle.dumps(zi_ff)

        pkl_2 = pickle.dumps(zi)
        zi_rt_2 = pickle.loads(pkl_2)
        self.assertIs(zi, zi_rt_2)


class TzPathTest(unittest.TestCase, TzPathUserMixin):
    module = zoneinfo

    @staticmethod
    @contextlib.contextmanager
    def python_tzpath_context(value):
        path_var = "PYTHONTZPATH"
        try:
            with OS_ENV_LOCK:
                old_env = os.environ.get(path_var, None)
                os.environ[path_var] = value
                yield
        finally:
            if old_env is None:
                del os.environ[path_var]
            else:
                os.environ[path_var] = old_env  # pragma: nocover

    def test_env_variable(self):
        """Tests that the environment variable works with set_tzpath"""
        new_paths = [
            ("", []),
            ("/etc/zoneinfo", ["/etc/zoneinfo"]),
            (f"/a/b/c{os.pathsep}/d/e/f", ["/a/b/c", "/d/e/f"]),
        ]

        for new_path_var, expected_result in new_paths:
            with self.python_tzpath_context(new_path_var):
                with self.subTest(tzpath=new_path_var):
                    self.module.set_tzpath()
                    tzpath = self.module.TZPATH
                    self.assertSequenceEqual(tzpath, expected_result)

    def test_tzpath_error(self):
        bad_values = [
            "/etc/zoneinfo:/usr/share/zoneinfo",
            b"/etc/zoneinfo:/usr/share/zoneinfo",
            0,
        ]

        for bad_value in bad_values:
            with self.subTest(value=bad_value):
                with self.assertRaises(TypeError):
                    self.module.set_tzpath(bad_value)

    def test_tzpath_attribute(self):
        tzpath_0 = ["/one", "/two"]
        tzpath_1 = ["/three"]

        with tzpath_context(tzpath_0):
            query_0 = self.module.TZPATH

        with tzpath_context(tzpath_1):
            query_1 = self.module.TZPATH

        self.assertSequenceEqual(tzpath_0, query_0)
        self.assertSequenceEqual(tzpath_1, query_1)


class TestModule(unittest.TestCase):
    module = zoneinfo

    def test_getattr_error(self):
        with self.assertRaises(AttributeError):
            self.module.NOATTRIBUTE

    def test_dir_contains_all(self):
        """dir(self.module) should at least contain everything in __all__."""
        module_all_set = set(self.module.__all__)
        module_dir_set = set(dir(self.module))

        difference = module_all_set - module_dir_set

        self.assertFalse(difference)

    def test_dir_unique(self):
        """Test that there are no duplicates in dir(self.module)"""
        module_dir = dir(self.module)
        module_unique = set(module_dir)

        self.assertCountEqual(module_dir, module_unique)


@dataclasses.dataclass
class ZoneOffset:
    tzname: str
    utcoffset: timedelta
    dst: timedelta = ZERO


@dataclasses.dataclass
class ZoneTransition:
    transition: datetime
    offset_before: ZoneOffset
    offset_after: ZoneOffset

    @property
    def transition_utc(self):
        return (self.transition - self.offset_before.utcoffset).replace(
            tzinfo=timezone.utc
        )

    @property
    def fold(self):
        """Whether this introduces a fold"""
        return self.offset_before.utcoffset > self.offset_after.utcoffset

    @property
    def gap(self):
        """Whether this introduces a gap"""
        return self.offset_before.utcoffset < self.offset_after.utcoffset

    @property
    def delta(self):
        return self.offset_after.utcoffset - self.offset_before.utcoffset

    @property
    def anomaly_start(self):
        if self.fold:
            return self.transition + self.delta
        else:
            return self.transition

    @property
    def anomaly_end(self):
        if not self.fold:
            return self.transition + self.delta
        else:
            return self.transition


class ZoneInfoData:
    def __init__(self, source_json, tzpath, v1=False):
        self.tzpath = pathlib.Path(tzpath)
        self.keys = []
        self.v1 = v1
        self._populate_tzpath(source_json)

    def path_from_key(self, key):
        return self.tzpath / key

    def _populate_tzpath(self, source_json):
        with open(source_json, "rb") as f:
            zoneinfo_dict = json.load(f)

        for key, value in zoneinfo_dict.items():
            self.keys.append(key)
            raw_data = self._decode_text(value)

            if self.v1:
                data = self._convert_to_v1(raw_data)
            else:
                data = raw_data

            destination = self.path_from_key(key)
            destination.parent.mkdir(exist_ok=True, parents=True)
            with open(destination, "wb") as f:
                f.write(data)

    def _decode_text(self, contents):
        raw_data = b"".join(map(str.encode, contents))
        decoded = base64.b85decode(raw_data)

        return lzma.decompress(decoded)

    def _convert_to_v1(self, contents):
        assert contents[0:4] == b"TZif", "Invalid TZif data found!"
        version = int(contents[5])

        header_start = 6 + 16
        header_end = header_start + 24  # 6l == 24 bytes
        assert version != 1, "Version 1 file found: no conversion necessary"
        isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = struct.unpack(
            ">6l", contents[header_start:header_end]
        )

        file_size = typecnt * 5 + charcnt * 6 + leapcnt * 8 + isstdcnt + isutcnt
        file_size += header_end
        out = b"TZif" + b"1" + contents[5 : (file_size + 5)]

        return out


class ZoneDumpData:
    @classmethod
    def transition_keys(cls):
        return cls._get_zonedump().keys()

    @classmethod
    def load_transition_examples(cls, key):
        return cls._get_zonedump()[key]

    # These are examples of a bunch of transitions that can be used in tests
    # The format for each transition is:
    #
    @classmethod
    def _get_zonedump(cls):
        if not cls._ZONEDUMP_DATA:
            cls._populate_zonedump_data()
        return cls._ZONEDUMP_DATA

    @classmethod
    def _populate_zonedump_data(cls):
        def _Africa_Casablanca():
            P00_s = ZoneOffset("+00", ZERO, ZERO)
            P01_d = ZoneOffset("+01", ONE_H, ONE_H)
            P00_d = ZoneOffset("+00", ZERO, -ONE_H)
            P01_s = ZoneOffset("+01", ONE_H, ZERO)

            return [
                # Morocco sometimes pauses DST during Ramadan
                ZoneTransition(datetime(2018, 3, 25, 2), P00_s, P01_d),
                ZoneTransition(datetime(2018, 5, 13, 3), P01_d, P00_s),
                ZoneTransition(datetime(2018, 6, 17, 2), P00_s, P01_d),
                # On October 28th Morocco set standard time to +01,
                # with negative DST only during Ramadan
                ZoneTransition(datetime(2018, 10, 28, 3), P01_d, P01_s),
                ZoneTransition(datetime(2019, 5, 5, 3), P01_s, P00_d),
                ZoneTransition(datetime(2019, 6, 9, 2), P00_d, P01_s),
            ]

        def _America_Los_Angeles():
            LMT = ZoneOffset("LMT", timedelta(seconds=-28378), ZERO)
            PST = ZoneOffset("PST", timedelta(hours=-8), ZERO)
            PDT = ZoneOffset("PDT", timedelta(hours=-7), ONE_H)
            PWT = ZoneOffset("PWT", timedelta(hours=-7), ONE_H)
            PPT = ZoneOffset("PPT", timedelta(hours=-7), ONE_H)

            return [
                ZoneTransition(datetime(1883, 11, 18, 12, 7, 2), LMT, PST),
                ZoneTransition(datetime(1918, 3, 31, 2), PST, PDT),
                ZoneTransition(datetime(1918, 3, 31, 2), PST, PDT),
                ZoneTransition(datetime(1918, 10, 27, 2), PDT, PST),
                # Transition to Pacific War Time
                ZoneTransition(datetime(1942, 2, 9, 2), PST, PWT),
                # Transition from Pacific War Time to Pacific Peace Time
                ZoneTransition(datetime(1945, 8, 14, 16), PWT, PPT),
                ZoneTransition(datetime(1945, 9, 30, 2), PPT, PST),
                ZoneTransition(datetime(2015, 3, 8, 2), PST, PDT),
                ZoneTransition(datetime(2015, 11, 1, 2), PDT, PST),
                # After 2038: Rules continue indefinitely
                ZoneTransition(datetime(2450, 3, 13, 2), PST, PDT),
                ZoneTransition(datetime(2450, 11, 6, 2), PDT, PST),
            ]

        def _America_Santiago():
            LMT = ZoneOffset("LMT", timedelta(seconds=-16966), ZERO)
            SMT = ZoneOffset("SMT", timedelta(seconds=-16966), ZERO)
            N05 = ZoneOffset("-05", timedelta(seconds=-18000), ZERO)
            N04 = ZoneOffset("-04", timedelta(seconds=-14400), ZERO)
            N03 = ZoneOffset("-03", timedelta(seconds=-10800), ONE_H)

            return [
                ZoneTransition(datetime(1890, 1, 1), LMT, SMT),
                ZoneTransition(datetime(1910, 1, 10), SMT, N05),
                ZoneTransition(datetime(1916, 7, 1), N05, SMT),
                ZoneTransition(datetime(2008, 3, 30), N03, N04),
                ZoneTransition(datetime(2008, 10, 12), N04, N03),
                ZoneTransition(datetime(2040, 4, 8), N03, N04),
                ZoneTransition(datetime(2040, 9, 2), N04, N03),
            ]

        def _Asia_Tokyo():
            JST = ZoneOffset("JST", timedelta(seconds=32400), ZERO)
            JDT = ZoneOffset("JDT", timedelta(seconds=36000), ONE_H)

            # Japan had DST from 1948 to 1951, and it was unusual in that
            # the transition from DST to STD occurred at 25:00, and is
            # denominated as such in the time zone database
            return [
                ZoneTransition(datetime(1948, 5, 2), JST, JDT),
                ZoneTransition(datetime(1948, 9, 12, 1), JDT, JST),
                ZoneTransition(datetime(1951, 9, 9, 1), JDT, JST),
            ]

        def _Australia_Sydney():
            LMT = ZoneOffset("LMT", timedelta(seconds=36292), ZERO)
            AEST = ZoneOffset("AEST", timedelta(seconds=36000), ZERO)
            AEDT = ZoneOffset("AEDT", timedelta(seconds=39600), ONE_H)

            return [
                ZoneTransition(datetime(1895, 2, 1), LMT, AEST),
                ZoneTransition(datetime(1917, 1, 1, 0, 1), AEST, AEDT),
                ZoneTransition(datetime(1917, 3, 25, 2), AEDT, AEST),
                ZoneTransition(datetime(2012, 4, 1, 3), AEDT, AEST),
                ZoneTransition(datetime(2012, 10, 7, 2), AEST, AEDT),
                ZoneTransition(datetime(2040, 4, 1, 3), AEDT, AEST),
                ZoneTransition(datetime(2040, 10, 7, 2), AEST, AEDT),
            ]

        def _Europe_Dublin():
            LMT = ZoneOffset("LMT", timedelta(seconds=-1500), ZERO)
            DMT = ZoneOffset("DMT", timedelta(seconds=-1521), ZERO)
            IST_0 = ZoneOffset("IST", timedelta(seconds=2079), ONE_H)
            GMT_0 = ZoneOffset("GMT", ZERO, ZERO)
            BST = ZoneOffset("BST", ONE_H, ONE_H)
            GMT_1 = ZoneOffset("GMT", ZERO, -ONE_H)
            IST_1 = ZoneOffset("IST", ONE_H, ZERO)

            return [
                ZoneTransition(datetime(1880, 8, 2, 0), LMT, DMT),
                ZoneTransition(datetime(1916, 5, 21, 2), DMT, IST_0),
                ZoneTransition(datetime(1916, 10, 1, 3), IST_0, GMT_0),
                ZoneTransition(datetime(1917, 4, 8, 2), GMT_0, BST),
                ZoneTransition(datetime(2016, 3, 27, 1), GMT_1, IST_1),
                ZoneTransition(datetime(2016, 10, 30, 2), IST_1, GMT_1),
                ZoneTransition(datetime(2487, 3, 30, 1), GMT_1, IST_1),
                ZoneTransition(datetime(2487, 10, 26, 2), IST_1, GMT_1),
            ]

        def _Europe_Lisbon():
            WET = ZoneOffset("WET", ZERO, ZERO)
            WEST = ZoneOffset("WEST", ONE_H, ONE_H)
            CET = ZoneOffset("CET", ONE_H, ZERO)
            CEST = ZoneOffset("CEST", timedelta(seconds=7200), ONE_H)

            return [
                ZoneTransition(datetime(1992, 3, 29, 1), WET, WEST),
                ZoneTransition(datetime(1992, 9, 27, 2), WEST, CET),
                ZoneTransition(datetime(1993, 3, 28, 2), CET, CEST),
                ZoneTransition(datetime(1993, 9, 26, 3), CEST, CET),
                ZoneTransition(datetime(1996, 3, 31, 2), CET, WEST),
                ZoneTransition(datetime(1996, 10, 27, 2), WEST, WET),
            ]

        def _Europe_London():
            LMT = ZoneOffset("LMT", timedelta(seconds=-75), ZERO)
            GMT = ZoneOffset("GMT", ZERO, ZERO)
            BST = ZoneOffset("BST", ONE_H, ONE_H)

            return [
                ZoneTransition(datetime(1847, 12, 1), LMT, GMT),
                ZoneTransition(datetime(2005, 3, 27, 1), GMT, BST),
                ZoneTransition(datetime(2005, 10, 30, 2), BST, GMT),
                ZoneTransition(datetime(2043, 3, 29, 1), GMT, BST),
                ZoneTransition(datetime(2043, 10, 25, 2), BST, GMT),
            ]

        def _Pacific_Kiritimati():
            LMT = ZoneOffset("LMT", timedelta(seconds=-37760), ZERO)
            N1040 = ZoneOffset("-1040", timedelta(seconds=-38400), ZERO)
            N10 = ZoneOffset("-10", timedelta(seconds=-36000), ZERO)
            P14 = ZoneOffset("+14", timedelta(seconds=50400), ZERO)

            # This is literally every transition in Christmas Island history
            return [
                ZoneTransition(datetime(1901, 1, 1), LMT, N1040),
                ZoneTransition(datetime(1979, 10, 1), N1040, N10),
                # They skipped December 31, 1994
                ZoneTransition(datetime(1994, 12, 31), N10, P14),
            ]

        cls._ZONEDUMP_DATA = {
            "Africa/Casablanca": _Africa_Casablanca(),
            "America/Los_Angeles": _America_Los_Angeles(),
            "America/Santiago": _America_Santiago(),
            "Australia/Sydney": _Australia_Sydney(),
            "Asia/Tokyo": _Asia_Tokyo(),
            "Europe/Dublin": _Europe_Dublin(),
            "Europe/Lisbon": _Europe_Lisbon(),
            "Europe/London": _Europe_London(),
            "Pacific/Kiritimati": _Pacific_Kiritimati(),
        }

    _ZONEDUMP_DATA = {}
