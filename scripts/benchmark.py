import statistics
import sys
import timeit
from datetime import datetime, timezone

import click
import pint
import pytz
from dateutil import tz

from backports.zoneinfo import ZoneInfo
from backports.zoneinfo._zoneinfo import ZoneInfo as PyZoneInfo

_PINT_REGISTRY = pint.UnitRegistry()
S = _PINT_REGISTRY.s

DATETIME = datetime(2020, 1, 1)
ZONE_DEFAULT_CONSTRUCTOR = {
    "c_zoneinfo": ZoneInfo,
    "py_zoneinfo": PyZoneInfo,
    "dateutil": tz.gettz,
    "pytz": pytz.timezone,
}

ZONE_NO_CACHE_CONSTRUCTOR = {
    "c_zoneinfo": ZoneInfo.no_cache,
    "py_zoneinfo": PyZoneInfo.no_cache,
    "dateutil": tz.gettz.nocache,
}

SOURCES = ["dateutil", "pytz"]

BENCHMARKS = {
    "to_utc": lambda *args, **kwargs: bench_astimezone(
        *args, **kwargs, from_utc=False
    ),
    "from_utc": lambda *args, **kwargs: bench_astimezone(
        *args, **kwargs, from_utc=True
    ),
    "utcoffset": lambda *args, **kwargs: bench_utcoffset(*args, **kwargs),
    "constructor": lambda *args, **kwargs: bench_constructor(
        *args, **kwargs, cache=True
    ),
    "no_cache_constructor": lambda *args, **kwargs: bench_constructor(
        *args, **kwargs, cache=False
    ),
    "constructor_strong_cache_miss": lambda *args, **kwargs: bench_constructor_strong_cache_miss(
        *args, **kwargs
    ),
}


def get_zone(source, key):
    return ZONE_DEFAULT_CONSTRUCTOR[source](key)


def bench_astimezone(source, zone_key, from_utc=True):
    zone = get_zone(source, zone_key)
    tz_from = timezone.utc
    tz_to = zone

    if not from_utc:
        tz_to, tz_from = tz_from, tz_to

    dt_from = DATETIME.replace(tzinfo=tz_from)

    def func(dt_from=dt_from, tz_to=tz_to):
        return dt_from.astimezone(tz_to)

    return func


def bench_utcoffset(source, zone_key):
    zone = get_zone(source, zone_key)
    base_dt = DATETIME
    if source != "pytz":
        dt = base_dt.replace(tzinfo=zone)
    else:
        dt = zone.localize(base_dt)

    def func(dt=dt):
        return dt.utcoffset()

    return func


def bench_constructor(source, zone_key, cache=False):
    if cache:
        zone_cache = get_zone(source, zone_key)
        constructor = ZONE_DEFAULT_CONSTRUCTOR[source]
    else:
        if source not in ZONE_NO_CACHE_CONSTRUCTOR:
            raise UnsupportedOperation(
                f"Source {source} does not support no-cache construction."
            )

        zone_cache = None
        constructor = ZONE_NO_CACHE_CONSTRUCTOR[source]

    def func(constructor=constructor, key=zone_key, _cache=zone_cache):
        constructor(key)

    return func


def bench_constructor_strong_cache_miss(source, zone_key):
    if source == "pytz":
        raise UnsupportedOperation("pytz does not have a strong and weak cache")

    # Strong cache is 8 keys
    keys = [
        "UTC",
        "America/New_York",
        "America/Los_Angeles",
        "America/Chicago",
        "Asia/Tokyo",
        "Europe/Lisbon",
        "Africa/Casablanca",
        "Australia/Sydney",
        "America/Denver",
        "Europe/London",
    ]

    if zone_key in keys:
        keys.append("Europe/Moscow")
    else:
        keys.append(zone_key)

    constructor = ZONE_DEFAULT_CONSTRUCTOR[source]

    def func(constructor=constructor, keys=keys):
        for key in keys:
            constructor(key)

    def setup(_cache=[]):
        _cache.clear()
        for key in keys:
            _cache.append(constructor(key))

    func.setup = setup

    return func


@click.command()
@click.option(
    "-b",
    "--benchmark",
    type=click.Choice(["all"] + list(BENCHMARKS.keys())),
    multiple=True,
)
@click.option(
    "-c", "--compare", type=click.Choice(["all"] + SOURCES), multiple=True
)
@click.option("--c_ext/--no_c_ext", default=True)
@click.option("--py/--no_py", default=True)
@click.option(
    "-z", "--zone", type=str, multiple=True, default=["America/New_York"]
)
def cli(benchmark, zone, compare, c_ext, py):
    """Runner for the benchmark suite"""

    # Assemble sources
    sources = []
    if c_ext:
        sources.append("c_zoneinfo")

    if py:
        sources.append("py_zoneinfo")

    if len(compare) == 1 and compare[0] == "all":
        sources.extend(SOURCES)
    else:
        for source in compare:
            if source == "all":
                raise InvalidInput(
                    'Cannot specify "all" along with other comparisons.'
                )
            sources.append(source)

    if not sources:
        raise InvalidInput("Nothing to benchmark specified!")

    # Determine which benchmarks to run
    if not benchmark:
        raise InvalidInput("No benchmarks specified")

    if len(benchmark) == 1 and benchmark[0] == "all":
        benchmarks = sorted(BENCHMARKS.keys())
    else:
        if "all" in benchmark:
            raise InvalidInput(
                '"all" cannot be specified with other benchmarks'
            )

        benchmarks = sorted(set(benchmark))

    zones = sorted(set(zone))

    sys.argv = sys.argv[0:1]
    main(sources, zones, benchmarks)


def run_benchmark(desc, func, k=5, N=None):
    timer = timeit.Timer(func, setup=getattr(func, "setup", lambda: None))

    # Run for 0.2 seconds
    if N is None:
        N, time_taken = timer.autorange()  # pylint: disable=unused-variable

    results = timer.repeat(repeat=k, number=N)
    results = [r / N for r in results]

    results_min = min(results)
    results_mean = statistics.mean(results)
    results_std = statistics.stdev(results, xbar=results_mean)

    results_mean *= S
    results_min *= S
    results_std *= S

    results_mean = results_mean.to_compact()
    results_min = results_min.to_compact()
    results_std = results_std.to_compact()

    print(
        f"{desc}: mean: {results_mean:.02f~P} ± {results_std:.02f~P}; "
        + f"min: {results_min:.02f~P} (k={k}, N={N})"
    )


def main(sources, zones, benchmarks):
    to_run = {}
    for benchmark in benchmarks:
        for zone in zones:
            to_run[(benchmark, zone)] = []
            for source in sources:
                func_factory = BENCHMARKS[benchmark]
                try:
                    func = func_factory(source, zone)
                except UnsupportedOperation:
                    continue

                to_run[(benchmark, zone)].append((source, func))

    for (benchmark, zone), funcs in to_run.items():
        print(f"Running {benchmark} in zone {zone}")
        for source, func in funcs:
            run_benchmark(f"{source}", func)

        print()


class InvalidInput(ValueError):
    """Raised for user input errors."""


class UnsupportedOperation(Exception):
    """Raised when a source doesn't support an operation."""


if __name__ == "__main__":
    try:
        cli()
    except InvalidInput as e:
        print(f"Invalid Input: {e}")
        sys.exit(1)
