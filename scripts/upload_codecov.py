"""
Simple script to gather coverage files and upload them to codecov.

Invoke with:

    python upload_codeov.py [COVERAGE_BASEDIR]
"""
import os
import pathlib
import subprocess
import sys


def main(base_dir):
    gcov_files = base_dir.glob(".gcov_coverage.*.xml")
    coverage_file = base_dir / "coverage.xml"

    coverage_files = list(gcov_files)
    if coverage_file.exists():
        coverage_files.append(coverage_file)

    coverage_files = list(map(os.fspath, coverage_files))

    subprocess.run(["codecov", "-f"] + coverage_files, check=True)


if __name__ == "__main__":
    base_dir = pathlib.Path(sys.argv[1])

    main(base_dir)
