"""
Script to automatically generate a JSON file containing time zone information.

This is done to allow "pinning" a small subset of the tzdata in the tests,
since we are testing properties of a file that may be subject to change. For
example, the behavior in the far future of any given zone is likely to change,
but "does this give the right answer for this file in 2040" is still an
important property to test.
"""
import base64
import json
import lzma
import pathlib
import textwrap

KEYS = [
    "Africa/Abidjan",
    "Africa/Casablanca",
    "America/Los_Angeles",
    "America/Santiago",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Europe/Dublin",
    "Europe/Lisbon",
    "Europe/London",
    "Pacific/Kiritimati",
    "UTC",
]

TZ_PATH = pathlib.Path("/usr/share/zoneinfo")
REPO_ROOT = pathlib.Path(__file__).parent.parent.absolute()
TEST_DATA_LOC = REPO_ROOT / "tests" / "data"


def get_zoneinfo(key):
    with open(TZ_PATH / key, "rb") as f:
        return f.read()


def get_v1_zoneinfo(key):
    with open(TZ_PATH / key, "rb") as f:
        return get_v1_zoneinfo_from_file(f)


def _get_v1_zoneinfo_from_file(f):
    """Get a zoneinfo file and ttruncate it to a version 1 file"""
    if stream.read(4) != b"TZif":
        raise ValueError("Invalid TZif file: magic not found")

    version = int(stream.read(1))

    if version == 1:
        f.seek(0)
        return f.read()

    isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = struct.unpack(
        ">6l", stream.read(24)
    )

    # Any version 2+ file starts with a version 1 file, so we can truncate
    # after that without issue.
    file_size = 5 + typecnt * 5 + charcnt * 6 + leapcnt * 8 + isstdcnt + isutcnt

    f.seek(0)
    out = bytearray(f.read(file_size))
    out[5] = b"1"

    return bytes(out)


def encode_compressed(data):
    compressed_zone = lzma.compress(data)
    raw = base64.b85encode(compressed_zone)

    data_str = raw.decode("utf-8")

    data_str = textwrap.wrap(data_str, width=70)
    return data_str


def load_compressed_keys():
    output = {key: encode_compressed(get_zoneinfo(key)) for key in KEYS}

    return output


def update_test_data(fname="zoneinfo_data.json"):
    TEST_DATA_LOC.mkdir(exist_ok=True, parents=True)

    json_kwargs = dict(indent=2, sort_keys=True,)

    output = load_compressed_keys()

    with open(TEST_DATA_LOC / fname, "w") as f:
        json.dump(output, f, **json_kwargs)


if __name__ == "__main__":
    update_test_data()
