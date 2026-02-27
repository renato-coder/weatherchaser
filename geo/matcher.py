"""Match county centroids against SPC risk polygons to find at-risk counties."""

from __future__ import annotations

import sys
import time
from collections import defaultdict

from shapely import STRtree
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon

from config import CountyRisk, County, RiskPolygon


def match_counties(
    outlooks: dict[int, list[RiskPolygon]],
    counties: list[County],
) -> dict[int, list[CountyRisk]]:
    """Match county centroids to SPC risk polygons. Returns day -> county risks."""
    if not counties:
        return {}

    # Build spatial index from county centroids
    centroids = [c.centroid for c in counties]
    tree = STRtree(centroids)

    results: dict[int, list[CountyRisk]] = {}

    for day in sorted(outlooks):
        polygons = outlooks[day]
        if not polygons:
            results[day] = []
            continue

        # Track risk per county FIPS for this day (to merge overlaps)
        day_risks: dict[str, CountyRisk] = {}

        for rp in polygons:
            # Expand MultiPolygon into individual polygons
            if isinstance(rp.geometry, MultiPolygon):
                sub_polys = list(rp.geometry.geoms)
            else:
                sub_polys = [rp.geometry]

            for poly in sub_polys:
                # Use STRtree to find centroids within this polygon
                try:
                    hit_indices = tree.query(poly, predicate="intersects")
                except GEOSException:
                    continue

                for idx in hit_indices:
                    county = counties[idx]
                    # Verify with precise within check
                    if not county.centroid.within(poly):
                        continue

                    fips = county.fips
                    if fips not in day_risks:
                        day_risks[fips] = CountyRisk(county=county, day=day)

                    _merge_risk(day_risks[fips], rp)

        results[day] = list(day_risks.values())

    return results


def _merge_risk(existing: CountyRisk, polygon: RiskPolygon) -> None:
    """Merge a new polygon match into an existing county risk (keep highest)."""
    if polygon.significant:
        existing.significant = True
        return

    if polygon.outlook_type == "categorical":
        existing.categorical_level = max(existing.categorical_level, polygon.risk_level)
    elif polygon.outlook_type == "hail":
        existing.hail_prob = max(existing.hail_prob, polygon.risk_level)
    elif polygon.outlook_type == "tornado":
        existing.tornado_prob = max(existing.tornado_prob, polygon.risk_level)
    elif polygon.outlook_type == "wind":
        existing.wind_prob = max(existing.wind_prob, polygon.risk_level)
    elif polygon.outlook_type == "probabilistic":
        # Day 3-8 combined probability — store in all applicable fields
        # Use as "any severe" indicator
        existing.hail_prob = max(existing.hail_prob, polygon.risk_level)
        existing.wind_prob = max(existing.wind_prob, polygon.risk_level)


def aggregate_by_state(county_risks: list[CountyRisk]) -> dict[str, dict]:
    """Group county risks by state abbreviation."""
    states: dict[str, list[CountyRisk]] = defaultdict(list)
    for cr in county_risks:
        states[cr.county.state_abbr].append(cr)

    summaries = {}
    for state_abbr, risks in sorted(states.items()):
        highest = max(cr.categorical_level for cr in risks) if risks else 0
        summaries[state_abbr] = {
            "count": len(risks),
            "highest_risk": highest,
            "counties": sorted(risks, key=lambda r: -r.categorical_level),
        }
    return summaries


if __name__ == "__main__":
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties

    print("Loading SPC outlooks...", file=sys.stderr)
    outlooks, any_data = fetch_spc_outlooks()

    print("Loading county boundaries...", file=sys.stderr)
    counties = load_counties()

    print("Matching counties to risk polygons...", file=sys.stderr)
    t0 = time.time()
    matched = match_counties(outlooks, counties)
    elapsed = time.time() - t0
    print(f"  Matching took {elapsed:.2f}s\n", file=sys.stderr)

    for day in sorted(matched):
        risks = matched[day]
        if not risks:
            print(f"  Day {day}: no counties matched")
            continue

        state_counts = defaultdict(int)
        for cr in risks:
            state_counts[cr.county.state_abbr] += 1

        state_str = ", ".join(f"{s} ({n})" for s, n in
                              sorted(state_counts.items(), key=lambda x: -x[1]))
        print(f"  Day {day}: {len(risks)} counties in {len(state_counts)} states — {state_str}")

    total = sum(len(v) for v in matched.values())
    print(f"\n  Total: {total} county-day matches")
