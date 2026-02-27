"""Rich console output for CAT Event Tracker results."""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config import RISK_NAMES, DayResult, CountyRisk
from classifier import risk_display_name


# Risk level → (Rich style, emoji)
RISK_STYLES: dict[int, tuple[str, str]] = {
    6: ("bold red",   "🔴"),   # HIGH
    5: ("red",        "🔴"),   # MODERATE
    4: ("dark_orange", "🟠"),  # ENHANCED
    3: ("yellow",     "🟡"),   # SLIGHT
    2: ("cyan",       "🔵"),   # MARGINAL
    1: ("dim",        "⚪"),   # TSTM
    0: ("green",      "🟢"),   # NONE/CLEAR
}


def render_console(results: list[DayResult], data_available: bool = True) -> None:
    """Render classified results to the terminal using Rich."""
    console = Console(file=sys.stdout)

    # Header
    now = datetime.now()
    timestamp = now.strftime("%b %d, %Y @ %I:%M %p")
    console.print()
    console.rule(f"[bold]REMI CAT TRACKER — {timestamp}[/bold]", style="bright_white")
    console.print()

    if not data_available:
        console.print(
            "[bold red]⚠️  WARNING: No SPC outlook data available. "
            "Cannot determine risk.[/bold red]"
        )
        console.print(
            "  Check https://www.spc.noaa.gov/products/outlook/ for status.\n"
        )
        return

    days_with_risk = sum(1 for dr in results if dr.county_risks)

    if days_with_risk == 0:
        console.print(
            "[bold green]🟢 No significant severe weather risk in the next 8 days.[/bold green]"
        )
        console.print("  All clear across CONUS.\n")
        return

    for dr in results:
        _render_day(console, dr)

    console.print()
    console.rule(
        f"[bold]Scan complete. {days_with_risk} day(s) with CAT-level risk.[/bold]",
        style="bright_white",
    )
    console.print()


def _render_day(console: Console, day_result: DayResult) -> None:
    """Render a single day's results."""
    day = day_result.day
    today = datetime.now().date()
    day_date = today + timedelta(days=day - 1)
    day_label = day_date.strftime("%a %b %d")

    if day == 1:
        header = f"📅 DAY 1 (Today — {day_label})"
    elif day == 2:
        header = f"📅 DAY 2 (Tomorrow — {day_label})"
    else:
        header = f"📅 DAY {day} ({day_label})"

    if not day_result.county_risks:
        console.print(f"  {header}")
        console.print("    ⚪ No significant risk\n")
        return

    # Group counties by risk level
    by_level: dict[int, list[CountyRisk]] = defaultdict(list)
    for cr in day_result.county_risks:
        # Use categorical level for grouping; if 0, infer from probabilities
        level = cr.categorical_level
        if level <= 1 and _has_prob_risk(cr):
            level = 2  # Group prob-only flags under MARGINAL for display
        by_level[level].append(cr)

    lines = []
    for level in sorted(by_level, reverse=True):
        risks = by_level[level]
        style, emoji = RISK_STYLES.get(level, ("white", "⚪"))
        level_name = risk_display_name(level)

        # Group by state
        state_risks: dict[str, list[CountyRisk]] = defaultdict(list)
        for cr in risks:
            state_risks[cr.county.state_abbr].append(cr)

        states_str = ", ".join(
            f"{s} ({len(crs)})" for s, crs in
            sorted(state_risks.items(), key=lambda x: -len(x[1]))
        )
        total_counties = len(risks)

        lines.append(Text.assemble(
            (f"  {emoji} {level_name} RISK", style),
            f" — {total_counties} counties",
        ))
        lines.append(Text(f"     States: {states_str}"))

        # Probability summary (max across all counties at this level)
        max_hail = max((cr.hail_prob for cr in risks), default=0)
        max_torn = max((cr.tornado_prob for cr in risks), default=0)
        max_wind = max((cr.wind_prob for cr in risks), default=0)
        sig = any(cr.significant for cr in risks)

        prob_parts = []
        if max_hail:
            prob_parts.append(f"Hail: {max_hail}%")
        if max_torn:
            prob_parts.append(f"Tornado: {max_torn}%")
        if max_wind:
            prob_parts.append(f"Wind: {max_wind}%")
        if sig:
            prob_parts.append("⚠ SIGNIFICANT")
        if prob_parts:
            lines.append(Text(f"     {' │ '.join(prob_parts)}"))

        # Top county names (up to 5 per state, top 3 states)
        top_states = sorted(state_risks.items(), key=lambda x: -len(x[1]))[:3]
        for state, crs in top_states:
            names = [cr.county.name for cr in crs[:5]]
            suffix = f" +{len(crs) - 5} more" if len(crs) > 5 else ""
            lines.append(Text(f"     {state}: {', '.join(names)}{suffix}", style="dim"))

        lines.append(Text(""))  # spacer

    # Build panel content
    content = Text("\n")
    for line in lines:
        content.append_text(line)
        content.append("\n")

    console.print(f"  {header}")
    console.print(Panel(content, expand=False, padding=(0, 2)))


def _has_prob_risk(cr: CountyRisk) -> bool:
    """Check if county has any probabilistic risk above threshold."""
    from config import CAT_THRESHOLDS
    return (
        cr.hail_prob >= CAT_THRESHOLDS["hail_prob_min"]
        or cr.tornado_prob >= CAT_THRESHOLDS["tornado_prob_min"]
        or cr.wind_prob >= CAT_THRESHOLDS["wind_prob_min"]
        or cr.significant
    )


if __name__ == "__main__":
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify

    print("Running full pipeline...", file=sys.stderr)
    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data)
    print(file=sys.stderr)
    render_console(results, data_available=any_data)
