"""Exercises the type stubs for the zoneinfo module."""
from __future__ import annotations

import io
import typing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence, Set, Tuple

import backports.zoneinfo as zoneinfo

REGISTERED_FUNCTIONS = []


def register(f: Callable[[], typing.Any]) -> Callable[[], typing.Any]:
    REGISTERED_FUNCTIONS.append(f)
    return f


@register
def test_constructor() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo("America/Los_Angeles")


def test_no_cache() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo.no_cache("America/Los_Angeles")


def test_from_file_accepts_bytes_io() -> zoneinfo.ZoneInfo:
    x = io.BytesIO(b"TZif")

    try:
        y = zoneinfo.ZoneInfo.from_file(x, key="BadZone")
    except ValueError:
        pass
    else:
        assert False

    return y


def test_clear_cache() -> Sequence[None]:
    y = zoneinfo.ZoneInfo.clear_cache(
        only_keys=["America/Los_Angeles", "Europe/Lisbon"]
    )
    assert y is None

    x = zoneinfo.ZoneInfo.clear_cache()
    assert x is None

    return (x, y)


def test_reset_tzpath() -> None:
    zoneinfo.reset_tzpath(to=[Path("/path/to/blah")])
    zoneinfo.reset_tzpath(to=["/path/to/blah"])
    zoneinfo.reset_tzpath(to=[])
    zoneinfo.reset_tzpath()


def test_offset() -> Sequence[
    Tuple[Optional[str], Optional[timedelta], Optional[timedelta]]
]:
    LA: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo("America/Los_Angeles")
    dt: datetime = datetime(2020, 1, 1, tzinfo=LA)

    offsets: typing.List[
        Tuple[Optional[str], Optional[timedelta], Optional[timedelta]]
    ] = []
    dt_offset = (dt.tzname(), dt.utcoffset(), dt.dst())
    assert dt_offset == ("PST", timedelta(hours=-8), timedelta(hours=0))
    offsets.append(dt_offset)

    t: time = time(0, tzinfo=LA)

    # TODO: Remove this cast when the bug in typeshed is fixed:
    # https://github.com/python/typeshed/pull/3964
    t_offset = (
        t.tzname(),
        t.utcoffset(),
        typing.cast(Optional[timedelta], t.dst()),
    )
    assert t_offset == (None, None, None)
    offsets.append(t_offset)

    return offsets


def test_astimezone() -> Sequence[datetime]:
    LA: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo("America/Los_Angeles")
    UTC: timezone = timezone.utc

    dt: datetime = datetime(2020, 1, 1, tzinfo=LA)
    dt_utc = dt.astimezone(UTC)
    dt_rt = dt_utc.astimezone(LA)

    assert dt == dt_rt
    assert dt == dt_utc

    return (dt, dt_rt, dt_utc)


def test_available_timezones() -> Set[str]:
    valid_zones = zoneinfo.available_timezones()

    assert "America/Los_Angeles" in valid_zones

    return valid_zones


def call_functions() -> None:
    for function in REGISTERED_FUNCTIONS:
        function()

    print("Success!")


if __name__ == "__main__":
    call_functions()
