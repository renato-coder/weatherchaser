"""Slack output — daily summary (Block Kit) and HTTP posting."""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta

import requests

from classifier import risk_display_name
from config import REMI_MARKETS, DayResult
from demand import DemandWindow, format_window
from markets import MarketResult
from sources.nws_alerts import NWSAlert, summarize_alerts

# Risk level → emoji
_RISK_EMOJI: dict[int, str] = {
    6: "\U0001f534",  # 🔴 HIGH
    5: "\U0001f534",  # 🔴 MODERATE
    4: "\U0001f7e0",  # 🟠 ENHANCED
    3: "\U0001f7e1",  # 🟡 SLIGHT
    2: "\U0001f535",  # 🔵 MARGINAL
    1: "\u26aa",      # ⚪ TSTM
    0: "\U0001f7e2",  # 🟢 NONE
}


# ---------------------------------------------------------------------------
# Message formatting — daily summary (Block Kit)
# ---------------------------------------------------------------------------

def _format_summary(
    results: list[DayResult],
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    nws_alerts: dict[str, list[NWSAlert]],
    scan_date: date,
    data_freshness: str = "",
) -> dict:
    """Format a full daily summary using Slack Block Kit."""
    now = datetime.now()
    timestamp = now.strftime("%b %d, %Y @ %-I:%M %p")

    blocks = []

    # Header
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"\U0001f329\ufe0f REMI CAT TRACKER — {timestamp}"},
    })

    # Day-by-day risk summary
    for dr in results:
        day = dr.day
        day_date = scan_date + timedelta(days=day - 1)

        if day == 1:
            day_header = f"\U0001f4c5 DAY 1 (Today — {day_date.strftime('%a %b %-d')})"
        elif day == 2:
            day_header = f"\U0001f4c5 DAY 2 (Tomorrow — {day_date.strftime('%a %b %-d')})"
        else:
            day_header = f"\U0001f4c5 DAY {day} ({day_date.strftime('%a %b %-d')})"

        if not dr.county_risks:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{day_header}*\n\u26aa No significant risk"},
            })
            continue

        # Group by risk level
        by_level: dict[int, list] = {}
        for cr in dr.county_risks:
            level = cr.categorical_level
            if level not in by_level:
                by_level[level] = []
            by_level[level].append(cr)

        lines = [f"*{day_header}*"]
        for level in sorted(by_level, reverse=True):
            crs = by_level[level]
            emoji = _RISK_EMOJI.get(level, "\u26aa")
            name = risk_display_name(level)

            # Group by state
            state_counts: dict[str, int] = {}
            for cr in crs:
                s = cr.county.state_abbr
                state_counts[s] = state_counts.get(s, 0) + 1
            states_str = ", ".join(
                f"{s} ({n})" for s, n in
                sorted(state_counts.items(), key=lambda x: -x[1])
            )

            lines.append(f"{emoji} *{name} RISK*: {states_str} ({len(crs)} counties)")

            max_hail = max((cr.hail_prob for cr in crs), default=0)
            max_torn = max((cr.tornado_prob for cr in crs), default=0)
            max_wind = max((cr.wind_prob for cr in crs), default=0)
            prob_parts = []
            if max_hail:
                prob_parts.append(f"Hail: {max_hail}%")
            if max_torn:
                prob_parts.append(f"Tornado: {max_torn}%")
            if max_wind:
                prob_parts.append(f"Wind: {max_wind}%")
            if prob_parts:
                lines.append(f"   \u2192 {' | '.join(prob_parts)}")

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        })

    # Divider before markets
    blocks.append({"type": "divider"})

    # Market summary
    market_lines = ["\U0001f3e2 *REMI MARKETS*"]

    # Build window lookup by market short_name
    window_lookup: dict[str, DemandWindow] = {}
    for w in demand_windows:
        window_lookup[w.market.short_name] = w

    # Flatten all MarketResults across days per market (use highest)
    market_best: dict[str, int] = {}
    for day, mrs in market_results.items():
        for mr in mrs:
            key = mr.market.short_name
            market_best[key] = max(market_best.get(key, 0), mr.highest_risk)

    for market in REMI_MARKETS:
        risk_level = market_best.get(market.short_name, 0)
        emoji = _RISK_EMOJI.get(risk_level, "\U0001f7e2")
        name = risk_display_name(risk_level)
        line = f"\u2022 {market.name}: {emoji} {name}"
        w = window_lookup.get(market.short_name)
        if w:
            line += f" \u2014 {format_window(w)}"
        market_lines.append(line)

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(market_lines)},
    })

    # NWS alerts summary
    all_alerts = summarize_alerts(nws_alerts)
    alert_parts = []
    for state, counts in sorted(all_alerts.items()):
        for evt, n in sorted(counts.items(), key=lambda x: -x[1]):
            alert_parts.append(f"{n} {evt}")
    if alert_parts:
        alert_states = "/".join(sorted(s for s, c in all_alerts.items() if c))
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"\u26a0\ufe0f *ACTIVE ALERTS:* {', '.join(alert_parts)} in {alert_states}",
            },
        })

    # Data freshness context block
    if data_freshness:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"\U0001f552 Data as of {data_freshness}"},
            ],
        })

    # Build fallback text
    market_count = sum(1 for v in market_best.values() if v >= 3)
    fallback = f"REMI CAT TRACKER — {market_count} market(s) at risk"

    return {"text": fallback, "blocks": blocks}


# ---------------------------------------------------------------------------
# HTTP posting
# ---------------------------------------------------------------------------

def _post_message(webhook_url: str, payload: dict) -> bool:
    """Post a message to a Slack webhook. Returns True on success."""
    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            print(f"  Slack rate limited, waiting {retry_after}s...", file=sys.stderr)
            time.sleep(retry_after)
            resp = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        if resp.status_code != 200:
            print(f"  Slack error {resp.status_code}: {resp.text}", file=sys.stderr)
            return False
        return True
    except requests.RequestException as exc:
        print(f"  Slack post failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def post_summary(
    results: list[DayResult],
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    nws_alerts: dict[str, list[NWSAlert]],
    webhook_url: str,
    scan_date: date | None = None,
    data_freshness: str = "",
) -> bool:
    """Post a full daily summary to Slack. Returns True on success."""
    if scan_date is None:
        scan_date = date.today()
    payload = _format_summary(results, market_results, demand_windows, nws_alerts,
                              scan_date, data_freshness=data_freshness)
    return _post_message(webhook_url, payload)


if __name__ == "__main__":
    import json as _json

    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from markets import classify_markets
    from demand import compute_windows

    print("Running full pipeline → Slack summary preview...", file=sys.stderr)

    outlooks, any_data = fetch_spc_outlooks()
    counties = load_counties()
    matched = match_counties(outlooks, counties)
    results = classify(matched, data_available=any_data)
    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    # Collect states from markets with risk
    risk_states: set[str] = set()
    for day, mrs in market_results.items():
        for mr in mrs:
            risk_states.update(mr.market.states)

    # Fetch NWS alerts for states with risk
    if risk_states:
        from sources.nws_alerts import fetch_alerts_for_states
        nws = fetch_alerts_for_states(sorted(risk_states))
    else:
        nws = {}

    print(file=sys.stderr)

    # Preview summary payload
    scan_date = date.today()
    payload = _format_summary(results, market_results, windows, nws, scan_date)
    print("=== Summary Payload (Block Kit) ===")
    print(_json.dumps(payload, indent=2))
