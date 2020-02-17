from datetime import datetime, timedelta, time, timezone
import io
import struct
import unittest
from zoneinfo import IANAZone


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
            # TODO: Dublin
        ]

        for tzstr, test_values in test_cases:
            tzi = self._zone_from_tzstr(tzstr)
            self.assertEqual(str(tzi), tzstr)

            for dt_naive, expected_tzname, expected_tzoffset in test_values:
                dt = dt_naive.replace(tzinfo=tzi)
                with self.subTest(tzstr=tzstr, dt=dt):
                    self.assertEqual(dt.tzname(), expected_tzname)
                    self.assertEqual(dt.utcoffset(), expected_tzoffset)
