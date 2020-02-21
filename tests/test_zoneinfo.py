from __future__ import annotations

import base64
import dataclasses
import importlib.metadata
import io
import lzma
import struct
import threading
import unittest
from datetime import datetime, time, timedelta, timezone

import zoneinfo
from zoneinfo import IANAZone

try:
    importlib.metadata.metadata("tzdata")
    HAS_TZDATA_PKG = True
except importlib.metadata.PackageNotFoundError:
    HAS_TZDATA_PKG = False

TZPATH_LOCK = threading.Lock()

# Useful constants
ZERO = timedelta(0)
ONE_H = timedelta(hours=1)


class IANAZoneTest(unittest.TestCase):
    def zone_from_key(self, key):
        f = ZoneDumpData.load_zoneinfo_file(key)
        return IANAZone.from_file(f, key=key)

    def zones(self):
        return ["Europe/Dublin", "America/Los_Angeles"]

    def test_unambiguous(self):
        test_cases = []
        for key in self.zones():
            for zone_transition in ZoneDumpData.load_transition_examples(key):
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

                self.assertEqual(dt.tzname(), offset.tzname)
                self.assertEqual(dt.utcoffset(), offset.utcoffset)
                self.assertEqual(dt.dst(), offset.dst)

    def test_folds_and_gaps(self):
        test_cases = []
        for key in self.zones():
            tests = {"folds": [], "gaps": []}
            for zt in ZoneDumpData.load_transition_examples(key):
                if zt.fold:
                    test_group = tests["folds"]
                elif zt.gap:
                    test_group = tests["gaps"]
                else:
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

                    self.assertEqual(dt.tzname(), offset.tzname)
                    self.assertEqual(dt.utcoffset(), offset.utcoffset)
                    self.assertEqual(dt.dst(), offset.dst)


class TzPathUserMixin:
    """
    Adds a setUp() and tearDown() to make TZ_PATHS manipulations thread-safe.

    Any tests that require manipulation of the TZ_PATHS global are necessarily
    thread unsafe, so we will acquire a lock and reset the TZ_PATHS variable
    to the default state before each test and release the lock after the test
    is through.
    """

    def setUp(self):
        TZPATH_LOCK.acquire()
        zoneinfo.set_tz_path()

    def tearDown(self):
        TZPATH_LOCK.release()


@unittest.skipIf(
    not HAS_TZDATA_PKG, "Skipping tzdata-specific tests: tzdata not installed"
)
class TZDataTests(IANAZoneTest, TzPathUserMixin):
    """
    Runs all the IANAZoneTest tests, but against the tzdata package

    NOTE: The ZoneDumpData has frozen test data, but tzdata will update, so
    some of the tests (particularly those related to the far future) may break
    in the event that the time zone policies in the relevant time zones change.
    """

    def setUp(self):
        super().setUp()
        self._old_tz_path = tuple(zoneinfo.TZPATHS)
        zoneinfo.set_tz_path([])

    def tearDown(self):
        zoneinfo.set_tz_path(self._old_tz_path)

    def zone_from_key(self, key):
        return IANAZone(key=key)


class TZStrTest(unittest.TestCase):
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

        return IANAZone.from_file(zonefile, key=tzstr)

    def test_m_spec_fromutc(self):
        UTC = timezone.utc
        test_cases = [
            (
                "EST5EDT,M3.2.0/4:00,M11.1.0/3:00",
                [
                    # fmt: off
                    (datetime(2019, 3, 9, 17), datetime(2019, 3, 9, 12)),
                    (datetime(2019, 3, 10, 8, 59), datetime(2019, 3, 10, 3, 59)),
                    (datetime(2019, 3, 10, 9, 0), datetime(2019, 3, 10, 5)),
                    (datetime(2019, 11, 2, 16, 0), datetime(2019, 11, 2, 12)),
                    (datetime(2019, 11, 3, 5, 59), datetime(2019, 11, 3, 1, 59)),
                    (datetime(2019, 11, 3, 6, 0), datetime(2019, 11, 3, 2)),
                    (datetime(2019, 11, 3, 7, 0), datetime(2019, 11, 3, 2, fold=1)),
                    (datetime(2019, 11, 3, 8, 0), datetime(2019, 11, 3, 3)),
                    # fmt: on
                ],
            ),
            # TODO: England, Australia, Dublin
        ]

        for tzstr, test_values in test_cases:
            tzi = self._zone_from_tzstr(tzstr)
            for dt_utc_naive, dt_local_naive in test_values:
                # Test conversion UTC -> TZ
                with self.subTest(
                    tzstr=tzstr, utc=dt_utc_naive, exp=dt_local_naive
                ):
                    dt_utc = dt_utc_naive.replace(tzinfo=UTC)
                    dt_actual = dt_utc.astimezone(tzi)
                    dt_actual_naive = dt_actual.replace(tzinfo=None)

                    self.assertEqual(dt_actual_naive, dt_local_naive)
                    self.assertEqual(dt_actual.fold, dt_local_naive.fold)

                # Test conversion TZ -> UTC
                with self.subTest(
                    tzstr=tzstr, local=dt_local_naive, utc=dt_utc_naive
                ):
                    dt_local = dt_local_naive.replace(tzinfo=tzi)
                    utc_expected = dt_utc_naive.replace(tzinfo=UTC)
                    utc_actual = dt_local.astimezone(UTC)

                    self.assertEqual(utc_actual, utc_expected)

    def test_m_spec_localized(self):
        """Tests that the Mm.n.d specification works"""
        # Test cases are a list of entries, where each entry is:
        # (tzstr, [(datetime, tzname, offset), ...])

        # TODO: Replace with tzstr + transitions?
        test_cases = [
            # Transition to EDT on the 2nd Sunday in March at 4 AM, and
            # transition back on the first Sunday in November at 3AM
            (
                "EST5EDT,M3.2.0/4:00,M11.1.0/3:00",
                [
                    # fmt: off
                    (datetime(2019, 3, 9), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 3, 59), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 4, 0, fold=0), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 4, 0, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 3, 10, 4, 1, fold=0), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 4, 1, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 2), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 3, 1, 59, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 3, 2, 0, fold=0), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 3, 2, 0, fold=1), "EST", timedelta(hours=-5)),
                    (datetime(2020, 3, 8, 3, 59), "EST", timedelta(hours=-5)),
                    (datetime(2020, 3, 8, 4, 0, fold=0), "EST", timedelta(hours=-5)),
                    (datetime(2020, 3, 8, 4, 0, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2020, 11, 1, 1, 59, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2020, 11, 1, 2, 0, fold=0), "EDT", timedelta(hours=-4)),
                    (datetime(2020, 11, 1, 2, 0, fold=1), "EST", timedelta(hours=-5)),
                    # fmt: on
                ],
            ),
            # Transition to BST happens on the last Sunday in March at 1 AM GMT
            # and the transition back happens the last Sunday in October at 2AM BST
            (
                "GMT0BST-1,M3.5.0/1:00,M10.5.0/2:00",
                [
                    # fmt: off
                    (datetime(2019, 3, 30), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 2, 0), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 26), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 0, 59, fold=1), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 1, 0, fold=0), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 2, 0), "BST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 0, 59, fold=1), "BST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 1, 0, fold=0), "BST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    # fmt: on
                ],
            ),
            # Austrialian time zone - DST start is chronologically first
            (
                "AEST-10AEDT,M10.1.0/2,M4.1.0/3",
                [
                    # fmt: off
                    (datetime(2019, 4, 6), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 1, 59), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 1, 59, fold=1), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 2, 0, fold=0), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 2, 1, fold=0), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 2, 0, fold=1), "AEST", timedelta(hours=10)),
                    (datetime(2019, 4, 7, 2, 1, fold=1), "AEST", timedelta(hours=10)),
                    (datetime(2019, 4, 7, 3, 0, fold=0), "AEST", timedelta(hours=10)),
                    (datetime(2019, 4, 7, 3, 0, fold=1), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 5, 0), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 6, 1, 59), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 6, 2, 0, fold=0), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 6, 2, 0, fold=1), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 10, 6, 3, 0), "AEDT", timedelta(hours=11)),
                    # fmt: on
                ],
            ),
            (
                "IST-1GMT0,M10.5.0,M3.5.0/1",
                [
                    # fmt: off
                    (datetime(2019, 3, 30), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 2, 0), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 26), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 0, 59, fold=1), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 1, 0, fold=0), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 2, 0), "IST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 0, 59, fold=1), "IST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 1, 0, fold=0), "IST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    # fmt: on
                ],
            ),
        ]

        for tzstr, test_values in test_cases:
            tzi = self._zone_from_tzstr(tzstr)
            self.assertEqual(str(tzi), tzstr)

            for dt_naive, expected_tzname, expected_tzoffset in test_values:
                dt = dt_naive.replace(tzinfo=tzi)
                with self.subTest(tzstr=tzstr, dt=dt):
                    self.assertEqual(dt.tzname(), expected_tzname)
                    self.assertEqual(dt.utcoffset(), expected_tzoffset)


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


BREAK_ONCE = 0


class ZoneDumpData:
    @classmethod
    def transition_keys(cls):
        return cls._get_zonedump().keys()

    @classmethod
    def load_transition_examples(cls, key):
        return cls._get_zonedump()[key]

    @classmethod
    def load_zoneinfo_file(cls, key):
        if key not in cls.ZONEFILES:
            raise ValueError(f"Zoneinfo file not found: {key}")

        raw = cls.ZONEFILES[key]
        raw = b"".join(map(bytes.strip, raw.split(b"\n")))
        decoded = base64.b85decode(raw)
        decompressed = lzma.decompress(decoded)

        return io.BytesIO(decompressed)

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
        # TODO: Australia, Brazil, London, Portugal, Kiribati
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

        cls._ZONEDUMP_DATA = {
            "America/Los_Angeles": _America_Los_Angeles(),
            "Europe/Dublin": _Europe_Dublin(),
        }

    _ZONEDUMP_DATA = {}

    ###
    #
    # Some example zoneinfo files: These are lzma-compressed and then b85 encoded
    # encoded, to minimize the space taken up in this file
    ZONEFILES = {
        "America/Los_Angeles": b"""
    {Wp48S^xk9=GL@E0stWa8~^|S5YJf5;0qH3OkDsf7KxBg5R*;z{h&-RlhRYu$%jt%!jv+IJxhE=%
    W1?wYb!37Rb?(rgwFIAQI{L#8r*zy!$TMtER_1(vn(Zix^{AVB1(jwr$iL6h0Z!28Gb~UW@0~e51
    2{Z%8}Qzdnjl~wJ1{c2>`Z@1A~t&lyL{p{eM{5)QGf7Mo5FW9==mlyXJt2UwpntR7H0eSq!(aYq#
    aqUz&RM*tvuMI)AsM?K3-dV3-TT{t)!Iy#JTo=tXkzAM9~j2YbiOls3(H8Dc>Y|D1aqL51vjLbpY
    G;GvGTQB4bXuJ%mA;(B4eUpu$$@zv2vVcq-Y)VKbzp^teiuzy}R{Luv<C;_cPe*n$Z<jeC9ogWF9
    =1mvvUYXS>DjpuVb`79O+CBmg{Wx!bvx$eu4zRE&PehMb=&G<9$>iZ|bFE)0=4I?KLFGBC0I(0_s
    vgw0%FiMsT%koo*!nEYc6GY@QnU}&4Isg;l=|khi(!VaiSE2=Ny`&&tpi~~;{$u<GHlsr3Ze!iYs
    U205RFKsLnrXwOL?Mq08xffgS{6hE|figx+&N%wbO}re@|}$l;g_6J-Wl%j|qev8A<T?NJ)`;2ne
    Gi_DHE4ET*W!c*ggPAgU+LE9=bH7;maCUikw^R)UM;TdVvNkQ;FGgN=yQER`SZ1nOgPXr0LCebLe
    ty&}kVdmVmB=8eSgtd!1%p=a2wooIL!Da}OPXvKBfRo?YxqS>N}%f|7mBhAy;<Er2&_LfND#qXN~
    Mkgf!@4VFAHr%$c)wrKA2cJYWK2>s3YT^sy!$eG~?`9mNJC9@4Bac_p^BZh)Yd_rWW5qh-?tKY(>
    5VHOL*iT8P@wCavLj^yYbnDR+4ukhS+xPrpl)iqB?u)bj9a2aW==g6G3lCJd>(+Blf<d4CF%7utl
    BUDki}J-!_Dy}5S(MrxSXy~$Z+hgH3P^<<w7D72L7I-R%H3(xm&q_DXxkp$owLTS6Wzkhc3nn;la
    ROa3)6hl&gH#)2Lif8fZe$@CdeJ-Zn&*>r)~^40F4f>cRZ^UF;RibfZ>0m73hRC{$vTfC(STN`g7
    (B<=Z2556{}0`?p&|Akkst!4Xy4OT;A@c$XTUI3FRRjy*KA7uC56FD)z^X{WV*sr(w!c$W357o!&
    eLO2wTDNOyw@gf(&R<<LF_3URI4=Ei`-%dM3T66j#9!aG7&b_@g1-9vo?DzXZ5vGaf~w__p_@_X?
    OdvQ_r5bvy2hpESTf+{p?jL+!~!{g8-<-5$@d8EZV&-5@a|;^1gB*R-~{EHFA-td_G2bt;~Y}>t;
    =-Tu1TV{>%8ZVATC9tjD8|(&`$9YHvZ9bVe#>w|8c;Tg|xE&)`*}LwM*E}q}q8^Qja%p`_U)*5Dd
    LI9O@!e=3jFjOCrCq28b_bb;s>%D#iJBCWJi{JH!Js;6nfayos$kq^OEX00HO-
    lokL0!mqm{vBYQl0ssI200dcD
    """,
        "Europe/Dublin": b"""
    {Wp48S^xk9=GL@E0stWa8~^|S5YJf5;0>b$_+0=h7KxBg5R*;&J77#T_U2R5sleVWFDmK~Kzj5oh
    @`<njquRZ&tJIS(cXp1>QKHvW^6V{jU-w>qg1tSt0c^vh;?qAqA0%t?;#S~6U8Qiv&f1s9IH#g$m
    1k1a#3+lylw4mwT4QnEUUQdwg+xnEcBlgu31bAVabn41OMZVLGz6NDwG%XuQar!b>GI{qSahE`AG
    }$kRWbuI~JCt;38)Xwbb~Qggs55t+MAHIxgDxzTJ;2xXx99+qCy445kC#v_l8fx|G&jlVvaciR<-
    wwf22l%4(t@S6tnX39#_K(4S0fu$FUs$isu<UOJYm|4)2iaEpsajn@}B#rnY=Cg_TXsm-A)*adXV
    &$klNTn3n{XXlaquu}6m{k%oRmY0Yyhlj*<W{D5m22}OiqnwHT!tnK`wPqx?wiF%v{ipTrOkcJ5P
    @7OC4(-l`*&SB$Wd4Vf8gn?>d<i@%mP*e*ttDj`9M1;9$YV@dhT)DVcwdq(Ly~KDm_&KL?{_mFww
    YtJqRZBk)i1FVQy!40w_KyAg?hIA=_{(3#S0eWsF8f%_4Zza$4@$lSmov+Huyn$vP^zJ|8-<C3#q
    #0kEs9cNg^xUR(m?wEWt-DGctAh2nIo~fz%$m$I41=b_WuJ6M9g#A9_Epwqw{d0B|vzmg#_y<=_>
    9IKzCXB<o`d)**5V6g!<<Jw1n5TrN-$)aYz4cLsTmpsUf-6L7ix+kk>78NkARYq@9Dc0TGkhz);N
    tM_SSzEffNl{2^*CKGdp52h!52A)6q9fUSltXF{T*Ehc9Q7u8!W7pE(Fv$D$cKUAt6wY=DA1mGgx
    C*VXq_If3G#FY6-Voj`fIKk`0}Cc72_SD{v>468LV{pyBI33^p0E?}RwDA6Pkq--C~0jF&Z@Pv!d
    x_1SN_)jwz@P$(oK%P!Tk9?fRjK88yxhxlcFtTjjZ$DYssSsa#ufYrR+}}nKS+r384o~!Uw$nwTb
    F~qgRsgr0N#d@KIinx%<pnyQ!|>hQB(SJyjJtDtIy(%mDm}ZBGN}dV6K~om|=UVGkbciQ=^$_14|
    gT21!YQ)@y*Rd0i_lS6gtPBE9+ah%WIJPwzUTjIr+J1XckkmA!6WE16%CVAl{Dn&-)=G$Bjh?bh0
    $Xt1UDcgXJjXzzojuw0>paV~?Sa`VN3FysqF<S*L0RYSAY3jt(8wCD04RfyEcP(RNT%x7k(7m-9H
    3{zuQ`RZy-Rz%*&dldDVFF+TwSAPO1wRX^5W5@xJ9{vWw?rc^NH({%Ie<rxKqSVy!Le-_`U&@W_(
    D+>xTzfKVAu*ucq#+m=|KSSMvp_#@-lwd+q*ueFQ^5<D+|jLr?k{O39i8AX2Qb^zi9A<7XD1y!-W
    2|0Hk8JVkN;gl><|<0R-u4qYMbRqzSn&Q7jSuvc%b+EZc%>nI(+&0Tl1Y>a6v4`uNFD-7$QrhHgS
    7Wnv~rDgfH;rQw3+m`LJxoM4v#gK@?|B{RHJ*VxZgk#!p<_&-sjxOda0YaiJ1UnG41VPv(Et%Elz
    KRMcO$AfgU+Xnwg5p2_+NrnZ1WfEj^fmHd^sx@%JWKkh#zaK0ox%rdP)zUmGZZnqmZ_9L=%6R8ib
    JH0bOT$AGhDo6{fJ?;_U;D|^>5by2ul@i4Zf()InfFN}00EQ=q#FPL>RM>svBYQl0ssI200dcD
    """,
    }
