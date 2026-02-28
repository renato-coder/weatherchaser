"""Download, cache, and parse US county boundaries with centroids."""

from __future__ import annotations

import json
import os
import sys

import requests
from shapely.geometry import shape

from config import (
    COUNTY_CACHE_PATH,
    COUNTY_GEOJSON_URL,
    DOWNLOAD_TIMEOUT,
    NON_CONUS_FIPS,
    STATE_FIPS,
    County,
)


def load_counties() -> list[County]:
    """Load county boundaries from cached GeoJSON. Downloads if not cached."""
    if not os.path.exists(COUNTY_CACHE_PATH):
        _download_counties(COUNTY_CACHE_PATH)
    return _parse_county_geojson(COUNTY_CACHE_PATH)


_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB safety limit


def _download_counties(dest: str) -> None:
    """Download county boundaries GeoJSON to dest path."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"  Downloading county boundaries (~25 MB)...", file=sys.stderr)

    try:
        resp = requests.get(COUNTY_GEOJSON_URL, timeout=DOWNLOAD_TIMEOUT, stream=True)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"\nFATAL: Cannot download county boundaries: {exc}", file=sys.stderr)
        sys.exit(1)

    # Check Content-Length if available
    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > _MAX_DOWNLOAD_BYTES:
        print(f"\nFATAL: County file too large ({int(content_length)} bytes)", file=sys.stderr)
        sys.exit(1)

    total = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > _MAX_DOWNLOAD_BYTES:
                f.close()
                os.remove(dest)
                print(f"\nFATAL: Download exceeded {_MAX_DOWNLOAD_BYTES // (1024*1024)} MB limit", file=sys.stderr)
                sys.exit(1)
            f.write(chunk)

    size_mb = total / (1024 * 1024)
    print(f"  Downloaded {size_mb:.1f} MB to {dest}", file=sys.stderr)


def _parse_county_geojson(path: str) -> list[County]:
    """Parse GeoJSON file into County dataclasses with centroids."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        # Corrupted cache — delete and re-download
        print(f"  County cache corrupted ({exc}), re-downloading...", file=sys.stderr)
        os.remove(path)
        _download_counties(path)
        with open(path) as f:
            data = json.load(f)

    counties: list[County] = []
    skipped = 0

    for feat in data.get("features", []):
        fips = str(feat.get("id", "")).zfill(5)
        state_fips = fips[:2]

        # Skip non-CONUS (Alaska, Hawaii, territories)
        if state_fips in NON_CONUS_FIPS:
            skipped += 1
            continue

        state_abbr = STATE_FIPS.get(state_fips)
        if state_abbr is None:
            skipped += 1
            continue

        props = feat.get("properties", {})
        name = props.get("NAME", f"County {fips}")

        geom_data = feat.get("geometry")
        if geom_data is None:
            skipped += 1
            continue

        try:
            geom = shape(geom_data)
            centroid = geom.centroid
        except (ValueError, TypeError):
            skipped += 1
            continue

        counties.append(County(
            fips=fips,
            name=name,
            state_fips=state_fips,
            state_abbr=state_abbr,
            centroid=centroid,
            geometry=geom,
        ))

    if skipped:
        print(f"  Skipped {skipped} non-CONUS/invalid entries", file=sys.stderr)

    return counties


if __name__ == "__main__":
    print("Loading county boundaries...", file=sys.stderr)
    counties = load_counties()

    # Summarize
    states: dict[str, int] = {}
    for c in counties:
        states[c.state_abbr] = states.get(c.state_abbr, 0) + 1

    print(f"\n  Parsed {len(counties)} CONUS counties across {len(states)} states")

    # Sample records
    samples = [c for c in counties if c.fips in ("48201", "36061", "06037", "17031", "40109")]
    if not samples:
        samples = counties[:3]
    for c in samples:
        print(f"  Sample: {c.fips} ({c.name}, {c.state_abbr}) "
              f"centroid: ({c.centroid.x:.2f}, {c.centroid.y:.2f})")

    # Top states by county count
    top = sorted(states.items(), key=lambda x: -x[1])[:10]
    print(f"\n  Top states: {', '.join(f'{s} ({n})' for s, n in top)}")
