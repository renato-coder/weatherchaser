"""Compute demand windows from market risk results."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta

from config import DEMAND_WINDOW_END_DAYS, DEMAND_WINDOW_START_DAYS, Market
from markets import MarketResult


@dataclass
class DemandWindow:
    """Projected demand spike window for a market after a storm."""
    market: Market
    storm_date: date
    window_start: date          # storm_date + DEMAND_WINDOW_START_DAYS
    window_end: date            # storm_date + DEMAND_WINDOW_END_DAYS
    trigger_day: int            # SPC day number (1-8) or 0 for confirmed
    highest_risk: int           # Max categorical level across days
    confirmed: bool = False     # True if NWS confirmed warnings exist


def compute_windows(
    market_results: dict[int, list[MarketResult]],
    scan_date: date | None = None,
) -> list[DemandWindow]:
    """Compute demand windows from market risk results.

    For multi-day storms in the same market, uses the FIRST day as storm_date
    to avoid duplicate windows.
    """
    if scan_date is None:
        scan_date = date.today()

    # Group by market short_name → list of (day, MarketResult)
    market_days: dict[str, list[tuple[int, MarketResult]]] = {}
    for day, mrs in market_results.items():
        for mr in mrs:
            key = mr.market.short_name
            if key not in market_days:
                market_days[key] = []
            market_days[key].append((day, mr))

    windows: list[DemandWindow] = []

    for short_name, day_results in market_days.items():
        if not day_results:
            continue

        # Sort by day number — use FIRST day as storm_date
        day_results.sort(key=lambda x: x[0])
        first_day, first_mr = day_results[0]

        storm_date_cal = scan_date + timedelta(days=first_day - 1)
        highest_risk = max(mr.highest_risk for _, mr in day_results)

        window = DemandWindow(
            market=first_mr.market,
            storm_date=storm_date_cal,
            window_start=storm_date_cal + timedelta(days=DEMAND_WINDOW_START_DAYS),
            window_end=storm_date_cal + timedelta(days=DEMAND_WINDOW_END_DAYS),
            trigger_day=first_day,
            highest_risk=highest_risk,
        )
        windows.append(window)

    # Sort by storm_date, then market name
    windows.sort(key=lambda w: (w.storm_date, w.market.short_name))
    return windows


def format_window(window: DemandWindow) -> str:
    """Return human-readable demand window string."""
    start = window.window_start.strftime("%b %-d")
    end = window.window_end.strftime("%b %-d")
    return f"Volume bump {start}–{end}"


if __name__ == "__main__":
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from markets import classify_markets

    print("Running full pipeline → demand windows...", file=sys.stderr)

    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data)
    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    print(file=sys.stderr)

    if not windows:
        print("  No markets at risk — no demand windows to compute.")
    else:
        for w in windows:
            status = " [CONFIRMED]" if w.confirmed else ""
            print(f"  {w.market.short_name}: storm Day {w.trigger_day} "
                  f"({w.storm_date}) → {format_window(w)}{status}")
        print(f"\n  {len(windows)} demand window(s) computed")
