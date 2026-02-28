"""Fetch and parse SPC convective outlook GeoJSON files for Days 1-8."""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict

import requests
from shapely.geometry import MultiPolygon, Polygon, shape

from config import (
    HTTP_TIMEOUT,
    RETRY_DELAY,
    SPC_RISK_LEVELS,
    SPC_URLS,
    RiskPolygon,
)


_fetch_metadata: dict[str, str] = {}


def get_fetch_metadata() -> dict[str, str]:
    """Return metadata from the most recent fetch (URL -> Last-Modified/Date header)."""
    return dict(_fetch_metadata)


def fetch_spc_outlooks() -> tuple[dict[int, list[RiskPolygon]], bool]:
    """Fetch all SPC outlook GeoJSON files for Days 1-8.

    Returns (outlooks_by_day, any_data_fetched).
    """
    _fetch_metadata.clear()
    outlooks: dict[int, list[RiskPolygon]] = defaultdict(list)
    success_count = 0

    for day, outlook_type, url in SPC_URLS:
        print(f"  Day {day} {outlook_type}... ", end="", file=sys.stderr)
        geojson = _fetch_geojson(url)

        if geojson is None:
            print("skipped", file=sys.stderr)
            continue

        features = geojson.get("features", [])
        if not features:
            print("0 polygons (empty)", file=sys.stderr)
            success_count += 1
            continue

        polygons = _parse_features(features, day, outlook_type)
        outlooks[day].extend(polygons)
        success_count += 1
        print(f"{len(polygons)} polygon(s)", file=sys.stderr)

    any_data = success_count > 0
    # Include all days 1-8 so downstream knows which days had data
    result: dict[int, list[RiskPolygon]] = {}
    for day in range(1, 9):
        result[day] = list(outlooks.get(day, []))
    return result, any_data


def _fetch_geojson(url: str) -> dict | None:
    """Fetch a single GeoJSON URL with one retry. Returns None on failure."""
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT)

            if resp.status_code == 404:
                print("404 ", end="", file=sys.stderr)
                return None

            if resp.status_code >= 500:
                if attempt == 0:
                    print(f"HTTP {resp.status_code}, retrying... ", end="", file=sys.stderr)
                    time.sleep(RETRY_DELAY)
                    continue
                print(f"HTTP {resp.status_code} ", end="", file=sys.stderr)
                return None

            if resp.status_code >= 400:
                print(f"HTTP {resp.status_code} ", end="", file=sys.stderr)
                return None

            # Capture freshness metadata
            last_mod = resp.headers.get("Last-Modified", resp.headers.get("Date", ""))
            if last_mod:
                _fetch_metadata[url] = last_mod

            return resp.json()

        except requests.exceptions.Timeout:
            if attempt == 0:
                print("timeout, retrying... ", end="", file=sys.stderr)
                time.sleep(RETRY_DELAY)
                continue
            print("timeout ", end="", file=sys.stderr)
            return None

        except requests.exceptions.ConnectionError:
            if attempt == 0:
                print("connection error, retrying... ", end="", file=sys.stderr)
                time.sleep(RETRY_DELAY)
                continue
            print("connection error ", end="", file=sys.stderr)
            return None

        except json.JSONDecodeError:
            print("bad JSON ", end="", file=sys.stderr)
            return None

        except requests.exceptions.RequestException as exc:
            print(f"error: {exc} ", end="", file=sys.stderr)
            return None

    return None


def _parse_features(features: list[dict], day: int, outlook_type: str) -> list[RiskPolygon]:
    """Parse GeoJSON features into RiskPolygon objects."""
    polygons = []

    for feat in features:
        geom_data = feat.get("geometry")
        if geom_data is None:
            continue

        props = feat.get("properties", {})
        label = str(props.get("LABEL", props.get("LABEL2", ""))).strip()
        if not label:
            continue

        try:
            geom = shape(geom_data)
        except (ValueError, TypeError):
            continue

        if not isinstance(geom, (Polygon, MultiPolygon)):
            continue

        # Detect SIGN/SIG features (significant severe hatching)
        is_significant = label.upper() in ("SIGN", "SIG")
        if is_significant:
            polygons.append(RiskPolygon(
                geometry=geom,
                day=day,
                outlook_type=outlook_type,
                label=label,
                risk_level=0,
                stroke=str(props.get("stroke", "")),
                fill=str(props.get("fill", "")),
                significant=True,
            ))
            continue

        risk_level = _label_to_risk_level(label, outlook_type)
        if risk_level is None:
            continue

        polygons.append(RiskPolygon(
            geometry=geom,
            day=day,
            outlook_type=outlook_type,
            label=label,
            risk_level=risk_level,
            stroke=str(props.get("stroke", "")),
            fill=str(props.get("fill", "")),
            significant=False,
        ))

    return polygons


def _label_to_risk_level(label: str, outlook_type: str) -> int | None:
    """Convert LABEL string to numeric risk level.

    For categorical: returns 1-6 (TSTM through HIGH).
    For probabilistic: returns percentage as int (5, 15, 30, etc.).
    Returns None if label cannot be parsed.
    """
    if outlook_type == "categorical":
        return SPC_RISK_LEVELS.get(label.upper())

    # Probabilistic: try to parse as number
    try:
        val = float(label)
    except ValueError:
        return SPC_RISK_LEVELS.get(label.upper())

    # Detect decimal fraction (0.05, 0.15) vs percentage (5, 15, 30)
    # SPC uses "0.15" for 15% or "15" for 15%; "1" means 1%, not 100%
    if 0 < val < 1.0:
        return int(val * 100)
    if val >= 1:
        return int(val)
    return None


if __name__ == "__main__":
    print("Fetching SPC outlooks...", file=sys.stderr)
    outlooks, any_data = fetch_spc_outlooks()

    if not any_data:
        print("\nWARNING: No SPC outlook data was available.")
        sys.exit(1)

    total = 0
    for day in sorted(outlooks):
        polys = outlooks[day]
        total += len(polys)
        sig_count = sum(1 for p in polys if p.significant)
        types = defaultdict(int)
        for p in polys:
            types[p.outlook_type] += 1
        type_str = ", ".join(f"{t}: {c}" for t, c in sorted(types.items()))
        sig_str = f" ({sig_count} significant)" if sig_count else ""
        print(f"  Day {day}: {len(polys)} polygon(s){sig_str} — {type_str}")

    print(f"\nTotal: {total} risk polygon(s) across {len(outlooks)} day(s)")
