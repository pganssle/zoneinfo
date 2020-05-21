import subprocess
import sys

from backports.zoneinfo import __version__ as VERSION


def get_current_tag():
    p = subprocess.run(
        ["git", "describe", "--tag"], check=True, stdout=subprocess.PIPE
    )

    return p.stdout.strip().decode()


if __name__ == "__main__":
    tag = get_current_tag()
    if tag != VERSION:
        print(f"Tag does not match version: {tag!r} != {VERSION!r}")
        sys.exit(1)
