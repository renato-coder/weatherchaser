"""AI-generated demand briefing using Claude Sonnet."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

import anthropic

from classifier import risk_display_name
from config import (
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_MODEL,
    BRIEFING_MAX_DAY,
    REMI_MARKETS,
    Market,
)
from demand import DemandWindow, format_window
from markets import MarketResult

_SYSTEM_PROMPT = """\
You write twice-weekly storm briefings for Remi, a roofing company.
You'll receive structured data about which metro markets have severe
weather risk this week and when demand might spike afterward.

Write a short Slack message (3-5 short paragraphs) that tells the
team what they need to know. Rules:

- Start with: 📋 Storm Brief — {day_of_week} {month} {day}
- Group active markets together. For each, write 1-2 plain English
  sentences: what's coming (hail, tornado risk, storms), when, and
  when demand would spike. Use the market owner's name if provided.
- Group quiet markets in one line: "{names} — all quiet."
- End with a casual sign-off like "That's it for this week."
- No weather jargon. No risk scores. No probability percentages.
  No FIPS codes. No county counts. Write like a teammate, not a
  dashboard.
- If data_freshness is provided, end with a note like "Data as of {data_freshness}."
- Keep it under 200 words."""

# Hazard labels for risk types
_HAZARD_LABELS: dict[str, str] = {
    "hail": "hail",
    "tornado": "tornado risk",
    "wind": "damaging winds",
}


def prepare_briefing_data(
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    scan_date: date | None = None,
    data_freshness: str = "",
) -> dict:
    """Serialize market risk data into a clean JSON structure for Claude."""
    if scan_date is None:
        scan_date = date.today()

    # Build window lookup
    window_lookup: dict[str, DemandWindow] = {}
    for w in demand_windows:
        window_lookup[w.market.short_name] = w

    # Track which markets have risk (Days 1-5 only)
    active: dict[str, dict] = {}

    for day in sorted(market_results):
        if day > BRIEFING_MAX_DAY:
            continue
        for mr in market_results[day]:
            short = mr.market.short_name
            day_date = scan_date + timedelta(days=day - 1)
            day_label = day_date.strftime("%a %b %-d")

            # Determine hazards
            hazards = []
            if mr.max_hail >= 15:
                hazards.append("hail")
            if mr.max_tornado >= 5:
                hazards.append("tornado risk")
            if mr.max_wind >= 15:
                hazards.append("damaging winds")
            if not hazards:
                hazards.append("storms")

            risk_name = risk_display_name(mr.highest_risk)
            day_info = {
                "day": day,
                "date": day_label,
                "risk": risk_name,
                "hazards": hazards,
            }

            if short not in active:
                active[short] = {
                    "name": mr.market.name,
                    "short_name": short,
                    "states": mr.market.states,
                    "owner": mr.market.owner,
                    "risk_days": [],
                    "demand_window": None,
                }
            active[short]["risk_days"].append(day_info)

    # Add demand windows
    for short, data in active.items():
        w = window_lookup.get(short)
        if w:
            start = w.window_start.strftime("%b %-d")
            end = w.window_end.strftime("%b %-d")
            data["demand_window"] = f"{start} – {end}"

    # Quiet markets = all REMI markets NOT in active
    active_names = set(active.keys())
    quiet = [m.name for m in REMI_MARKETS if m.short_name not in active_names]

    result = {
        "scan_date": scan_date.isoformat(),
        "briefing_day": scan_date.strftime("%A"),
        "active_markets": list(active.values()),
        "quiet_markets": quiet,
    }
    if data_freshness:
        result["data_freshness"] = data_freshness
    return result


def generate_briefing(briefing_data: dict) -> str | None:
    """Call Claude Sonnet to generate a demand briefing.

    Returns the briefing text, or None on failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY not set. Cannot generate briefing.",
              file=sys.stderr)
        return None

    client = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=2)

    user_message = json.dumps(briefing_data, indent=2)

    try:
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = message.content[0].text

        # Log token usage
        usage = message.usage
        print(f"  Briefing generated ({usage.input_tokens} in / "
              f"{usage.output_tokens} out tokens)", file=sys.stderr)

        return text

    except anthropic.AuthenticationError:
        print("  ERROR: Invalid ANTHROPIC_API_KEY.", file=sys.stderr)
        return None
    except anthropic.RateLimitError:
        print("  ERROR: Anthropic rate limit exceeded. Try again later.",
              file=sys.stderr)
        return None
    except anthropic.APIConnectionError as exc:
        print(f"  ERROR: Could not reach Anthropic API: {exc}", file=sys.stderr)
        return None
    except anthropic.APIStatusError as exc:
        print(f"  ERROR: Anthropic API error {exc.status_code}: {exc.message}",
              file=sys.stderr)
        return None


def post_briefing(briefing_text: str, webhook_url: str) -> bool:
    """Post a briefing to Slack. Returns True on success."""
    from output.slack import _post_message

    payload = {"text": briefing_text}
    return _post_message(webhook_url, payload)


# ---------------------------------------------------------------------------
# Briefing validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result of validating a generated briefing against input data."""
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def validate_briefing(text: str, briefing_data: dict) -> ValidationResult:
    """Validate that a generated briefing matches the input data."""
    result = ValidationResult()
    text_lower = text.lower()

    # 1. Every active market name/short_name should appear in the briefing
    for market in briefing_data.get("active_markets", []):
        name = market.get("name", "")
        short = market.get("short_name", "")
        if name.lower() not in text_lower and short.lower() not in text_lower:
            result.errors.append(f"Active market '{name}' ({short}) not mentioned in briefing")
            result.passed = False

    # 2. Quiet markets should not appear near risk language (unless "quiet"/"all quiet" context)
    risk_words = {"risk", "storm", "hail", "tornado", "damaging", "severe", "threat"}
    for market_name in briefing_data.get("quiet_markets", []):
        name_lower = market_name.lower()
        if name_lower not in text_lower:
            continue
        # Find the position(s) of the market name and check surrounding context
        idx = text_lower.find(name_lower)
        while idx != -1:
            # Check 100 chars around the mention
            context_start = max(0, idx - 50)
            context_end = min(len(text_lower), idx + len(name_lower) + 50)
            context = text_lower[context_start:context_end]
            # OK if "quiet" or "all quiet" is nearby
            if "quiet" in context or "clear" in context:
                break
            # Warning if risk words are nearby
            if any(w in context for w in risk_words):
                result.warnings.append(
                    f"Quiet market '{market_name}' appears near risk language"
                )
                break
            idx = text_lower.find(name_lower, idx + 1)

    # 3. No probability percentages (e.g., "15%", "30%")
    pct_matches = re.findall(r'\d+%', text)
    if pct_matches:
        result.warnings.append(f"Probability percentages found: {', '.join(pct_matches)}")

    # 4. No county counts (e.g., "12 counties", "3 counties")
    county_matches = re.findall(r'\d+\s+counties', text_lower)
    if county_matches:
        result.warnings.append(f"County counts found: {', '.join(county_matches)}")

    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from config import BRIEFING_CATEGORICAL_MIN
    from markets import classify_markets
    from demand import compute_windows

    print("Running briefing pipeline...", file=sys.stderr)

    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data,
                       categorical_min=BRIEFING_CATEGORICAL_MIN)

    # Filter to Days 1-5
    results = [dr for dr in results if dr.day <= BRIEFING_MAX_DAY]

    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    scan_date = date.today()
    data = prepare_briefing_data(market_results, windows, scan_date)

    print(file=sys.stderr)
    print("=== Briefing Data (JSON) ===")
    print(json.dumps(data, indent=2))

    print("\n=== Generating Briefing ===")
    text = generate_briefing(data)
    if text:
        print(text)
    else:
        print("(Failed to generate briefing — see errors above)")
