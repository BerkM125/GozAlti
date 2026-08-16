#!/usr/bin/env bash
# Friday job. Produces the offline basemap. Run once, commit nothing (it's big).
#
# Prereqs:
#   brew install pmtiles        (or grab the release binary from github.com/protomaps/go-pmtiles)
#
# 1. Find the current daily build filename on https://docs.protomaps.com/basemaps/downloads
#    and set BUILD below. They are dated, e.g. 20260810.pmtiles
set -euo pipefail

BUILD="${BUILD:-}"
if [ -z "$BUILD" ]; then
  echo "Set BUILD to the current daily build URL from the Protomaps downloads page." >&2
  echo "  BUILD=https://build.protomaps.com/YYYYMMDD.pmtiles ./scripts/build-tiles.sh" >&2
  exit 1
fi

BBOX="-122.355,47.595,-122.315,47.625"   # downtown Seattle; keep in sync with src/config.ts
MAXZOOM=16                                # each extra level ~doubles the file

mkdir -p public/tiles public/assets
pmtiles extract "$BUILD" public/tiles/seattle-dt.pmtiles \
  --bbox="$BBOX" --maxzoom="$MAXZOOM"

echo
echo "Tiles: $(du -h public/tiles/seattle-dt.pmtiles | cut -f1)"
echo
echo "NOW DO THE PART EVERYONE FORGETS:"
echo "  Download the fonts and sprites ZIPs from github.com/protomaps/basemaps-assets"
echo "  Unzip into public/assets/fonts/ and public/assets/sprites/"
echo "  Without these your labels vanish the moment the venue wifi dies."
