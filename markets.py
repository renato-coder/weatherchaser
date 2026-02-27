"""Classify Remi markets by joining county-level risk data to market FIPS sets."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from config import REMI_MARKETS, CountyRisk, DayResult, Market


@dataclass
class MarketResult:
    """Risk assessment for a single Remi market on a single day."""
    market: Market
    day: int
    highest_risk: int = 0
    affected_counties: int = 0
    total_counties: int = 0
    max_hail: int = 0
    max_tornado: int = 0
    max_wind: int = 0
    significant: bool = False
    county_risks: list[CountyRisk] = field(default_factory=list)


def classify_markets(
    results: list[DayResult],
    markets: list[Market] | None = None,
) -> dict[int, list[MarketResult]]:
    """Join DayResult county risks to market definitions.

    Returns day -> list of MarketResults (only markets with risk included).
    """
    if markets is None:
        markets = REMI_MARKETS

    market_results: dict[int, list[MarketResult]] = {}

    for dr in results:
        day = dr.day
        day_markets: list[MarketResult] = []

        # Build FIPS lookup for this day's flagged counties
        fips_to_risk: dict[str, CountyRisk] = {}
        for cr in dr.county_risks:
            fips_to_risk[cr.county.fips] = cr

        for market in markets:
            fips_set = set(market.fips_codes)
            matched = [fips_to_risk[f] for f in fips_set if f in fips_to_risk]

            if not matched:
                continue

            mr = MarketResult(
                market=market,
                day=day,
                highest_risk=max(cr.categorical_level for cr in matched),
                affected_counties=len(matched),
                total_counties=len(market.fips_codes),
                max_hail=max(cr.hail_prob for cr in matched),
                max_tornado=max(cr.tornado_prob for cr in matched),
                max_wind=max(cr.wind_prob for cr in matched),
                significant=any(cr.significant for cr in matched),
                county_risks=matched,
            )
            day_markets.append(mr)

        market_results[day] = day_markets

    return market_results


if __name__ == "__main__":
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify, risk_display_name

    print("Running full pipeline → market classification...", file=sys.stderr)

    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data)
    market_results = classify_markets(results)

    print(file=sys.stderr)

    days_with_markets = 0
    for day in sorted(market_results):
        mrs = market_results[day]
        if not mrs:
            continue
        days_with_markets += 1
        print(f"  Day {day}:")
        for mr in sorted(mrs, key=lambda m: -m.highest_risk):
            risk = risk_display_name(mr.highest_risk)
            print(f"    {mr.market.short_name}: {risk} — "
                  f"{mr.affected_counties}/{mr.total_counties} counties "
                  f"(hail:{mr.max_hail}% torn:{mr.max_tornado}% wind:{mr.max_wind}%)")

    if days_with_markets == 0:
        print("  No Remi markets at CAT-level risk.")
    else:
        print(f"\n  {days_with_markets} day(s) with market-level risk")
