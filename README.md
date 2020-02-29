# `zoneinfo`: IANA Time Zones for the Standard Library

This is the reference implementation for [PEP 615](https://www.python.org/dev/peps/pep-0615/), which proposes support for the IANA time zone database in the standard library.

It exposes the `zoneinfo` module, which uses system time zone data if available, and otherwise falls back to using the [`tzdata`](https://github.com/pganssle/tzdata) package if installed.

## Use

To use this, construct a `ZoneInfo` object and attach it to your datetime:

```python
>>> from zoneinfo import ZoneInfo
>>> from datetime import datetime, timedelta, timezone
>>> dt = datetime(1992, 3, 1, tzinfo=ZoneInfo("Europe/Minsk"))
>>> print(dt)
1992-03-01 00:00:00+02:00
>>> print(dt.utcoffset())
2:00:00
>>> print(dt.tzname())
EET
```

Arithmetic works as expected without the need for a "normalization" step:

```python
>>> dt += timedelta(days=90)
>>> print(dt)
1992-05-30 00:00:00+03:00
>>> dt.utcoffset()
datetime.timedelta(seconds=10800)
>>> dt.tzname()
'EEST'
```

Ambiguous and imaginary times are handled using the `fold` attribute added in [PEP 495](https://www.python.org/dev/peps/pep-0495/):

```python
>>> dt = datetime(2020, 11, 1, 1, tzinfo=ZoneInfo("America/Chicago"))
>>> print(dt)
2020-11-01 01:00:00-05:00
>>> print(dt.replace(fold=1))
2020-11-01 01:00:00-06:00

>>> UTC = timezone.utc
>>> print(dt.astimezone(UTC))
2020-11-01 06:00:00+00:00
>>> print(dt.replace(fold=1).astimezone(UTC))
2020-11-01 07:00:00+00:00
```

# Contributing

Currently we are not accepting contributions to this library because we have not put the CLA in place and we would like to avoid complicating the process of adoption into the standard library.
