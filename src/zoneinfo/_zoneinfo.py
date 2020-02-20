import bisect
import calendar
import importlib.resources
import os
import re
import struct
from datetime import datetime, timedelta, timezone, tzinfo

TZPATHS = [
    "/usr/share/zoneinfo",
    "/usr/lib/zoneinfo",
    "/usr/share/lib/zoneinfo",
    "/etc/zoneinfo",
]

EPOCH = datetime(1970, 1, 1)
EPOCHORDINAL = datetime(1970, 1, 1).toordinal()

# It is relatively expensive to construct new timedelta objects, and in most
# cases we're looking at the same deltas, like integer numbers of hours, etc.
# To improve speed and memory use, we'll keep a dictionary with references
# to the ones we've already used so far.
# TODO: Make this a weak value dictionary
_DELTA_CACHE = {}


def _load_timedelta(seconds):
    return _DELTA_CACHE.setdefault(seconds, timedelta(seconds=seconds))


class IANAZone(tzinfo):
    def __init__(self, key):
        self._key = key
        self._file_path = self._find_tzfile(key)

        if self._file_path is not None:
            with open(self._file_path, "rb") as f:
                self._load_file(f)
        else:
            self._load_tzdata(key)

    @classmethod
    def from_file(cls, fobj, key=None):
        obj = cls.__new__(cls)
        obj._key = key
        obj._file_path = None
        obj._load_file(fobj)

        return obj

    def _load_tzdata(self, key):
        # TODO: Proper error for malformed keys?
        components = key.split("/")
        package_name = ".".join(["tzdata.zoneinfo"] + components[:-1])
        resource_name = components[-1]

        try:
            fobj = importlib.resources.open_binary(package_name, resource_name)
        except (ImportError, FileNotFoundError) as e:
            raise ValueError(f"No time zone found with key {key}") from e

        with fobj as f:
            self._load_file(f)

    # TODO: Handle `datetime.time` in these calls
    def utcoffset(self, dt):
        return self._find_trans(dt).utcoff

    def dst(self, dt):
        return self._find_trans(dt).dstoff

    def tzname(self, dt):
        return self._find_trans(dt).tzname

    def fromutc(self, dt):
        """Convert from datetime in UTC to datetime in local time"""

        if not isinstance(dt, datetime):
            raise TypeError("fromutc() requires a datetime argument")
        if dt.tzinfo is not self:
            raise ValueError("dt.tzinfo is not self")

        timestamp = self._get_local_timestamp(dt)

        # TODO: Why [1]?
        if len(self._trans_utc) >= 2 and timestamp < self._trans_utc[1]:
            tti = self._tti_before
            fold = 0
        elif timestamp > self._trans_utc[-1]:
            if isinstance(self._tz_after, _ttinfo):
                tti = self._tz_after
            else:
                tti, fold = self._tz_after.get_trans_info_fromutc(
                    timestamp, dt.year
                )
        else:
            idx = bisect.bisect_right(self._trans_utc, timestamp)

            tti_prev, tti = self._ttinfos[idx - 2 : idx]

            # Detect fold
            shift = tti_prev[0] - tti[0]
            fold = shift > timedelta(0, timestamp - self.trans_utc[idx - 1])
        dt += tti.utcoff
        if fold:
            return dt.replace(fold=1)
        else:
            return dt

    def _find_trans(self, dt):
        ts = self._get_local_timestamp(dt)

        lt = self._trans_local[dt.fold]

        if ts < lt[0]:
            return self._tti_before
        elif ts > lt[-1]:
            return self._tz_after.get_trans_info(ts, dt.year, dt.fold)
        else:
            # idx is the transition that occurs after this timestamp, so we
            # subtract off 1 to get the current ttinfo
            idx = bisect.bisect_right(lt, ts) - 1
            assert idx >= 0
            return self._ttinfos[idx]

    def _get_local_timestamp(self, dt):
        return (
            (dt.toordinal() - EPOCHORDINAL) * 86400
            + dt.hour * 3600
            + dt.minute * 60
            + dt.second
        )

    def __str__(self):
        if self._key is not None:
            return f"{self._key}"
        else:
            return repr(self)

    def __repr__(self):
        return f"{self.__class__.__name__}(file_path={self._file_path!r}, key={self._key!r})"

    def _find_tzfile(self, key):
        for search_path in TZPATHS:
            filepath = os.path.join(search_path, key)
            if os.path.isfile(filepath):
                return filepath

        return None

    def _load_file(self, fobj):
        # Retrieve all the data as it exists in the zoneinfo file
        trans_idx, trans_utc, utcoff, isdst, abbr, tz_str = self._load_data(
            fobj
        )

        # Infer the DST offsets (needed for .dst()) from the data
        dstoff = self._utcoff_to_dstoff(trans_idx, utcoff, isdst)

        # Convert all the transition times (UTC) into "seconds since 1970-01-01 local time"
        trans_local = self._ts_to_local(trans_idx, trans_utc, utcoff)

        # Construct `_ttinfo` objects for each transition in the file
        _ttinfo_list = [
            _ttinfo(
                _load_timedelta(utcoffset), _load_timedelta(dstoffset), tzname
            )
            for utcoffset, dstoffset, tzname in zip(utcoff, dstoff, abbr)
        ]

        self._trans_utc = trans_utc
        self._trans_local = trans_local
        self._ttinfos = [_ttinfo_list[idx] for idx in trans_idx]

        # Find the first non-DST transition
        for idx in range(len(isdst)):
            if not isdst[idx]:
                self._tti_before = self._ttinfos[idx]
                break
        else:
            if self._ttinfos:
                self._tti_before = self._ttinfos[0]
            else:
                self._tti_before = None

        # Set the "fallback" time zone
        if tz_str is not None:
            self._tz_after = _parse_tz_str(tz_str)
        else:
            self._tz_after = self._ttinfos[-1]

    @classmethod
    def _load_data(cls, fobj):
        header = _TZifHeader.from_file(fobj)

        if header.version == 1:
            time_size = 4
            time_type = "l"
        else:
            # Version 2+ has 64-bit integer transition times
            time_size = 8
            time_type = "q"

            # Version 2+ also starts with a Version 1 header and data, which
            # we need to skip now
            skip_bytes = (
                header.timecnt * 5
                + header.typecnt * 6  # Transition times and types
                + header.charcnt  # Local time type records
                + header.leapcnt * 8  # Time zone designations
                + header.isstdcnt  # Leap second records
                + header.isutcnt  # Standard/wall indicators  # UT/local indicators
            )

            fobj.seek(skip_bytes, 1)

            # Now we need to read the second header, which is not the same
            # as the first
            header = _TZifHeader.from_file(fobj)

        typecnt = header.typecnt
        timecnt = header.timecnt
        charcnt = header.charcnt

        # The data portion starts with timecnt transitions and indices
        if timecnt:
            trans_list_utc = struct.unpack(
                f">{timecnt}{time_type}", fobj.read(timecnt * time_size)
            )
            trans_idx = struct.unpack(f">{timecnt}B", fobj.read(timecnt))
        else:
            trans_list_utc = []
            trans_idx = []

        # Read the ttinfo struct, (utoff, isdst, abbrind)
        if typecnt:
            utcoff, isdst, abbrind = zip(
                *(struct.unpack(">lbb", fobj.read(6)) for i in range(typecnt))
            )
        else:
            utcoff = ()
            isdst = ()
            abbrind = ()

        # Now read the abbreviations. They are null-terminated strings, indexed
        # not by position in the array but by position in the unsplit
        # abbreviation string. I suppose this makes more sense in C, which uses
        # null to terminate the strings, but it's inconvenient here...
        char_total = 0
        abbr_vals = {}
        for abbr in fobj.read(charcnt).decode().split("\x00"):
            abbr_vals[char_total] = abbr
            char_total += len(abbr) + 1

        abbr = [abbr_vals[idx] for idx in abbrind]

        # The remainder of the file consists of leap seconds (currently unused) and
        # the standard/wall and ut/local indicators, which are metadata we don't need.
        # In version 2 files, we need to skip the unnecessary data to get at the TZ string:
        if header.version >= 2:
            # Each leap second record has size (time_size * 4)
            skip_bytes = header.isutcnt + header.isstdcnt + header.leapcnt * 32
            fobj.seek(skip_bytes, 1)

            c = fobj.read(1)  # Should be \n
            assert c == b"\n", c

            tz_bytes = b""
            # TODO: Walrus operator
            while True:
                c = fobj.read(1)
                if c == b"\n":
                    break
                tz_bytes += c

            tz_str = tz_bytes.decode()
        else:
            tz_str = None

        return trans_idx, trans_list_utc, utcoff, isdst, abbr, tz_str

    @staticmethod
    def _utcoff_to_dstoff(trans_idx, utcoffsets, isdsts):
        # Now we must transform our ttis and abbrs into `__ttinfo` objects,
        # but there is an issue: .dst() must return a timedelta with the
        # difference between utcoffset() and the "standard" offset, but
        # the "base offset" and "DST offset" are not encoded in the file;
        # we can infer what they are from the isdst flag, but it is not
        # sufficient to to just look at the last standard offset, because
        # occasionally countries will shift both DST offset and base offset.

        typecnt = len(isdsts)
        dstoffs = [0] * typecnt  # Provisionally assign all to 0.
        dst_cnt = sum(isdsts)
        dst_found = 0

        for idx in range(1, len(trans_idx)):
            if dst_cnt == dst_found:
                break

            dst = isdsts[idx]

            # We're only going to look at daylight saving time
            if not dst:
                continue

            # Skip any offsets that have already been assigned
            if dstoffs[idx] != 0:
                continue

            dstoff = 0
            utcoff = utcoffsets[idx]

            comp_idx = idx - 1

            if not isdsts[comp_idx]:
                dstoff = utcoff - utcoffsets[comp_idx]

            if not dstoff and idx < (typecnt - 1):
                comp_idx = idx + 1

                # If the following transition is also DST and we couldn't
                # find the DST offset by this point, we're going ot have to
                # skip it and hope this transition gets assigned later
                if isdsts[comp_idx]:
                    continue

                dstoff = utcoff - utcoffsets[comp_idx]

            if dstoff:
                dst_found += 1
                dstoffs[idx] = dstoff
        else:
            # If we didn't find a valid value for a given index, we'll end up
            # with dstoff = 0 for something where `isdst=1`. This is obviously
            # wrong - one hour will be a much better guess than 0
            for idx in range(typecnt):
                if not dstoffs[idx] and isdsts[idx]:
                    dstoffs[idx] = 3600

        return dstoffs

    @staticmethod
    def _ts_to_local(trans_idx, trans_list_utc, utcoffsets):
        """Generate number of seconds since 1970 *in the local time*.

        This is necessary to easily find the transition times in local time"""
        if not trans_list_utc:
            return []

        # Start with the timestamps and modify in-place
        trans_list_wall = [list(trans_list_utc), list(trans_list_utc)]

        offset = utcoffsets[0]
        trans_list_wall[0][0] += offset
        trans_list_wall[1][0] += offset

        for i in range(1, len(trans_idx)):
            offset_0 = utcoffsets[trans_idx[i - 1]]
            offset_1 = utcoffsets[trans_idx[i]]

            if offset_1 > offset_0:
                offset_1, offset_0 = offset_0, offset_1

            trans_list_wall[0][i] += offset_0
            trans_list_wall[1][i] += offset_1

        return trans_list_wall


class _ttinfo:
    __slots__ = ["utcoff", "dstoff", "tzname"]

    def __init__(self, utcoff, dstoff, tzname):
        self.utcoff = utcoff
        self.dstoff = dstoff
        self.tzname = tzname


class _TZStr:
    __slots__ = (
        "std",
        "dst",
        "start",
        "end",
        "get_trans_info",
        "get_trans_info_fromutc",
        "dst_diff",
    )

    def __init__(
        self, std_abbr, std_offset, dst_abbr, dst_offset, start=None, end=None
    ):
        self.dst_diff = dst_offset - std_offset
        std_offset = _load_timedelta(std_offset)
        self.std = _ttinfo(
            utcoff=std_offset, dstoff=_load_timedelta(0), tzname=std_abbr
        )

        self.start = start
        self.end = end

        if dst_abbr is not None:
            dst_offset = _load_timedelta(dst_offset)
            delta = _load_timedelta(self.dst_diff)
            self.dst = _ttinfo(utcoff=dst_offset, dstoff=delta, tzname=dst_abbr)

            if start is None or end is None:
                raise ValueError("Must specify start and end for TZStr")
            self.get_trans_info = self._get_trans_info_dst
            self.get_trans_info_fromutc = self._get_trans_info_dst_fromutc
        else:
            self.dst = None
            self.dst_offset = 0
            self.get_trans_info = self._get_trans_info_static
            self.get_trans_info_fromutc = self._get_trans_info_static

    def transitions(self, year):
        start = self.start.year_to_epoch(year)
        end = self.end.year_to_epoch(year)
        return start, end

    def _get_trans_info_static(self, ts):
        """Get the information about the current transition - fold and tti"""
        return self.std

    def _get_trans_info_dst(self, ts, year, fold):
        """Get the information about the current transition - tti"""
        start, end = self.transitions(year)

        # With fold = 0, the period (denominated in local time) with the
        # smaller offset starts at the end of the gap and ends at the end of
        # the fold; with fold = 1, it runs from the start of the gap to the
        # beginning of the fold.
        #
        # So in order to determine the DST boundaries we need to know both
        # the fold and whether DST is positive or negative (rare), and it
        # turns out that this boils down to fold XOR is_positive.
        if fold == (self.dst_diff >= 0):
            end -= self.dst_diff
        else:
            start += self.dst_diff

        if start < end:
            isdst = start <= ts < end
        else:
            isdst = not (end <= ts < start)

        return self.dst if isdst else self.std

    def _get_trans_info_dst_fromutc(self, ts, year):
        start, end = self.transitions(year)
        start -= self.std.utcoff.total_seconds()
        end -= self.dst.utcoff.total_seconds()

        if start < end:
            isdst = start <= ts < end
        else:
            isdst = not (end <= ts < start)

        # For positive DST, the ambiguous period is one dst_diff after the end
        # of DST; for negative DST, the ambiguous period is one dst_diff before
        # the start of DST.
        ambig_start = end if self.dst_diff > 0 else end
        fold = ambig_start <= ts < ambig_start + self.dst_diff

        return (self.dst if isdst else self.std, fold)


def _parse_tz_str(tz_str):
    # The tz string has the format:
    #
    # std[offset[dst[offset],start[/time],end[/time]]]
    #
    # std and dst must be 3 or more characters long and must not contain
    # a leading colon, embedded digits, commas, nor a plus or minus signs;
    # The spaces between "std" and "offset" are only for display and are
    # not actually present in the string.
    #
    # The format of the offset is ``[+|-]hh[:mm[:ss]]``

    offset_str, *start_end_str = tz_str.split(",", 1)

    # fmt: off
    parser_re = re.compile(
        r"(?P<std>[^0-9:.+-]+)" +
        r"((?P<stdoff>[+-]?\d{1,2}(:\d{2}(:\d{2})?)?)" +
            r"((?P<dst>[^0-9:.+-]+)" +
                r"((?P<dstoff>[+-]?\d{1,2}(:\d{2}(:\d{2})?)?))?" +
            r")?" + # dst
        r")?$" # stdoff
    )
    # fmt: on

    m = parser_re.match(offset_str)

    if m is None:
        raise ValueError(f"{tz_str} is not a valid TZ string")

    std_abbr = m.group("std")
    dst_abbr = m.group("dst")
    dst_offset = None

    if std_offset := m.group("stdoff"):
        std_offset = _parse_tz_delta(std_offset)
    else:
        std_offset = 0

    if dst_abbr is not None:
        if dst_offset := m.group("dstoff"):
            dst_offset = _parse_tz_delta(dst_offset)
        else:
            dst_offset = std_offset + 3600

        if not start_end_str:
            raise ValueError("Missing transition rules")

        start_end_strs = start_end_str[0].split(",", 1)
        start, end = (_parse_dst_start_end(x) for x in start_end_strs)

        return _TZStr(std_abbr, std_offset, dst_abbr, dst_offset, start, end)
    elif start_end_str:
        raise ValueError("Transition rule present without DST")
    else:
        # This is a static ttinfo, don't return _TZStr
        return _ttinfo(
            _load_timedelta(std_offset), _load_timedelta(0), std_abbr
        )


def _post_epoch_days_before_year(year):
    """Get the number of days between 1970-01-01 and YEAR-01-01"""
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400 - EPOCHORDINAL


class _day_offset:
    __slots__ = ["d", "julian", "hour", "minute", "second"]

    def __init__(self, d, julian, hour=2, minute=0, second=0):
        if julian:
            d -= 1

        if not 0 <= d <= 365:
            if julian:
                min_day = 1
                d += 1
            else:
                min_day = 0
            raise ValueError(f"d must be in [{min_day}, 365], not: {d}")

        self.d = d
        self.julian = julian
        self.hour = hour
        self.minute = minute
        self.second = second

    def year_to_epoch(self, year):
        days_before_year = _post_epoch_days_before_year(year)

        d = self.d
        if self.julian and d >= 59 and calendar.isleap(year):
            d += 1

        epoch = days_before_year * 86400
        epoch += self.hour * 3600 + self.minute * 60 + self.second

        return epoch


class _calendar_offset:
    __slots__ = ["m", "w", "d", "hour", "minute", "second"]

    _DAYS_BEFORE_MONTH = (
        -1,
        0,
        31,
        59,
        90,
        120,
        151,
        181,
        212,
        243,
        273,
        304,
        334,
    )

    def __init__(self, m, w, d, hour=2, minute=0, second=0):
        # if not 0 <= m <= 12:
        #     raise ValueError("m must be in [0, 12]")

        if not 0 < w <= 5:
            raise ValueError("w must be in (0, 5]")

        if not 0 <= d <= 6:
            raise ValueError("d must be in [0, 6]")

        self.m = m
        self.w = w
        self.d = d
        self.hour = hour
        self.minute = minute
        self.second = second

    @classmethod
    def _ymd2ord(cls, year, month, day):
        return (
            _post_epoch_days_before_year(year)
            + cls._DAYS_BEFORE_MONTH[month]
            + (month > 2 and calendar.isleap(year))
            + day
        )

    # TODO: These are not actually epoch dates as they are expressed in local time
    def year_to_epoch(self, year):
        """Calculates the datetime of the occurrence from the year"""
        # We know year and month, we need to convert w, d into day of month
        #
        # Week 1 is the first week in which day `d` (where 0 = Sunday) appears.
        # Week 5 represents the last occurrence of day `d`, so we need to know
        # the range of the month.
        first_day, days_in_month = calendar.monthrange(year, self.m)

        # This equation seems magical, so I'll break it down:
        # 1. calendar says 0 = Monday, POSIX says 0 = Sunday
        #    so we need first_day + 1 to get 1 = Monday -> 7 = Sunday,
        #    which is still equivalent because this math is mod 7
        # 2. Get first day - desired day mod 7: -1 % 7 = 6, so we don't need
        #    to do anything to adjust negative numbers.
        # 3. Add 1 because month days are a 1-based index.
        month_day = (self.d - (first_day + 1)) % 7 + 1

        # Now use a 0-based index version of `w` to calculate the w-th
        # occurrence of `d`
        month_day += (self.w - 1) * 7

        # month_day will only be > days_in_month if w was 5, and `w` means
        # "last occurrence of `d`", so now we just check if we over-shot the
        # end of the month and if so knock off 1 week.
        if month_day > days_in_month:
            month_day -= 7

        ordinal = self._ymd2ord(year, self.m, month_day)
        epoch = ordinal * 86400
        epoch += self.hour * 3600 + self.minute * 60 + self.second
        return epoch


def _parse_dst_start_end(dststr):
    date, *time = dststr.split("/")
    if date[0] == "M":
        n_is_julian = False
        m = re.match(r"M(\d{1,2})\.(\d).(\d)$", date)
        if m is None:
            raise ValueError(f"Invalid dst start/end date: {dststr}")
        date_offset = tuple(map(int, m.groups()))
        offset = _calendar_offset(*date_offset)
    else:
        if date[0] == "J":
            n_is_julian = True
            date = date[1:]
        else:
            n_is_julian = False

        doy = int(date)
        offset = _date_offset(doy, n_is_julian)

    if time:
        time_components = list(map(int, time[0].split(":")))
        n_components = len(time_components)
        if n_components < 3:
            time_components.extend([0] * (3 - n_components))
        offset.hour, offset.minute, offset.second = time_components

    return offset


def _parse_tz_delta(tz_delta):
    match = re.match(
        r"(?P<sign>[+-])?(?P<h>\d{1,2})(:(?P<m>\d{2})(:(?P<s>\d{2}))?)?",
        tz_delta,
    )
    if match is None:
        raise ValueError(f"{tz_delta} is not a valid offset")

    h, m, s = (
        int(v) if v is not None else 0
        for v in map(match.group, ("h", "m", "s"))
    )
    total = h * 3600 + m * 60 + s

    # Yes, +5 maps to an offset of -5h
    if match.group("sign") != "-":
        total *= -1

    return total


class _TZifHeader:
    __slots__ = [
        "version",
        "isutcnt",
        "isstdcnt",
        "leapcnt",
        "timecnt",
        "typecnt",
        "charcnt",
    ]

    def __init__(self, *args):
        assert len(self.__slots__) == len(args)
        for attr, val in zip(self.__slots__, args):
            setattr(self, attr, val)

    @classmethod
    def from_file(cls, stream):
        # The header starts with a 4-byte "magic" value
        if stream.read(4) != b"TZif":
            raise ValueError("Invalid TZif file: magic not found")

        version = int(stream.read(1))
        stream.read(15)

        args = (version,)

        # Slots are defined in the order that the bytes are arranged
        args = args + struct.unpack(">6l", stream.read(24))

        return cls(*args)
