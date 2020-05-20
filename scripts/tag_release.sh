#!/usr/bin/bash
#
# Script to tag the repository with the current version of the library
set -e

VERSION_LINE=$(grep '__version__ =' 'src/backports/zoneinfo/_version.py' -m 1)
VERSION=$(echo "$VERSION_LINE" | sed 's/__version__ = "\([^"]\+\)"/\1/')
echo "Found version: $VERSION"

git tag -s -m "Version $VERSION" $VERSION || exit "Failed to tag!"
echo "Success"
