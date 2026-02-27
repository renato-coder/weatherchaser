"""Export classified results to CSV with market and demand window columns."""

from __future__ import annotations

import csv
import sys
from datetime import date, timedelta

from config import DayResult, RISK_NAMES
from demand import DemandWindow
from markets import MarketResult


def export_csv(
    path: str,
    results: list[DayResult],
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    scan_date: date | None = None,
) -> None:
    """Export results to CSV file.

    One row per flagged county per day, with market and demand window columns.
    """
    if scan_date is None:
        scan_date = date.today()

    # Build FIPS → market name lookup
    fips_to_market: dict[str, str] = {}
    for day, mrs in market_results.items():
        for mr in mrs:
            for fips in mr.market.fips_codes:
                fips_to_market[fips] = mr.market.short_name

    # Build market → demand window lookup
    window_lookup: dict[str, DemandWindow] = {}
    for w in demand_windows:
        window_lookup[w.market.short_name] = w

    fieldnames = [
        "date", "day_number", "state", "county", "fips",
        "risk_level", "risk_name", "hail_prob", "tornado_prob", "wind_prob",
        "significant", "market", "demand_window_start", "demand_window_end",
    ]

    row_count = 0
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for dr in results:
            day_date = scan_date + timedelta(days=dr.day - 1)

            for cr in dr.county_risks:
                market_name = fips_to_market.get(cr.county.fips, "")
                window = window_lookup.get(market_name)

                writer.writerow({
                    "date": day_date.isoformat(),
                    "day_number": dr.day,
                    "state": cr.county.state_abbr,
                    "county": cr.county.name,
                    "fips": cr.county.fips,
                    "risk_level": cr.categorical_level,
                    "risk_name": RISK_NAMES.get(cr.categorical_level, ""),
                    "hail_prob": cr.hail_prob,
                    "tornado_prob": cr.tornado_prob,
                    "wind_prob": cr.wind_prob,
                    "significant": cr.significant,
                    "market": market_name,
                    "demand_window_start": window.window_start.isoformat() if window else "",
                    "demand_window_end": window.window_end.isoformat() if window else "",
                })
                row_count += 1

    print(f"  Exported {row_count} rows to {path}", file=sys.stderr)


if __name__ == "__main__":
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from markets import classify_markets
    from demand import compute_windows

    print("Running full pipeline → CSV export...", file=sys.stderr)

    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data)
    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    print(file=sys.stderr)

    out_path = "data/scan_export.csv"
    export_csv(out_path, results, market_results, windows)
    print(f"\n  CSV written to {out_path}")
