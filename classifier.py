"""Apply CAT thresholds to matched county risks and produce classified results."""

from __future__ import annotations

import sys
from collections import defaultdict

from config import CAT_THRESHOLDS, RISK_NAMES, CountyRisk, DayResult
from geo.matcher import aggregate_by_state


def classify(
    matched: dict[int, list[CountyRisk]],
    data_available: bool = True,
    categorical_min: int | None = None,
) -> list[DayResult]:
    """Apply CAT thresholds and return classified results per day.

    Pass categorical_min to override the default spc_categorical_min threshold.
    """
    results: list[DayResult] = []

    for day in sorted(matched):
        county_risks = matched[day]

        # Filter to counties meeting at least one threshold
        flagged = [cr for cr in county_risks
                   if _meets_threshold(cr, categorical_min=categorical_min)]

        # Sort by risk (highest first)
        flagged.sort(key=lambda cr: (
            -cr.categorical_level,
            -cr.hail_prob,
            -cr.tornado_prob,
            -cr.wind_prob,
            -int(cr.significant),
        ))

        state_summaries = aggregate_by_state(flagged) if flagged else {}

        results.append(DayResult(
            day=day,
            county_risks=flagged,
            state_summaries=state_summaries,
            data_available=data_available,
        ))

    return results


def _meets_threshold(risk: CountyRisk, categorical_min: int | None = None) -> bool:
    """Check if a county risk meets any CAT threshold."""
    if risk.significant:
        return True
    cat_min = categorical_min if categorical_min is not None else CAT_THRESHOLDS["spc_categorical_min"]
    if risk.categorical_level >= cat_min:
        return True
    if risk.hail_prob >= CAT_THRESHOLDS["hail_prob_min"]:
        return True
    if risk.tornado_prob >= CAT_THRESHOLDS["tornado_prob_min"]:
        return True
    if risk.wind_prob >= CAT_THRESHOLDS["wind_prob_min"]:
        return True
    return False


def risk_display_name(level: int) -> str:
    """Convert numeric risk level to display string."""
    return RISK_NAMES.get(level, f"LEVEL {level}")


if __name__ == "__main__":
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties

    print("Running full pipeline: fetch → match → classify...", file=sys.stderr)

    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data)

    print(file=sys.stderr)
    days_with_risk = 0

    for dr in results:
        if not dr.county_risks:
            continue
        days_with_risk += 1

        # Count by classification
        level_counts: dict[str, int] = defaultdict(int)
        for cr in dr.county_risks:
            name = risk_display_name(cr.categorical_level)
            level_counts[name] += 1

        level_str = ", ".join(f"{n}: {c}" for n, c in
                              sorted(level_counts.items(), key=lambda x: -x[1]))
        print(f"  Day {dr.day}: {len(dr.county_risks)} counties flagged ({level_str})")

        for state, info in dr.state_summaries.items():
            print(f"    {state}: {info['count']} counties "
                  f"(highest: {risk_display_name(info['highest_risk'])})")

    if days_with_risk == 0:
        if any_data:
            print("  No counties meet CAT thresholds. All clear.")
        else:
            print("  WARNING: No SPC data available. Cannot determine risk.")
    else:
        print(f"\n  {days_with_risk} day(s) with CAT-level risk")
