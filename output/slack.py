"""Slack output — alert triggers and daily summary."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

import requests

from classifier import risk_display_name
from config import REMI_MARKETS, RISK_NAMES, DayResult, Market
from demand import DemandWindow, format_window
from markets import MarketResult
from sources.nws_alerts import NWSAlert, has_confirmed_warnings, summarize_alerts

# Alert type escalation order
_ALERT_LEVELS = {"heads_up": 1, "plan_for_it": 2, "it_happened": 3}

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

_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "alert_state.json")


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    """Load alert state from JSON file. Returns empty dict if missing/corrupt."""
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """Write state atomically (write to temp, rename)."""
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_STATE_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _should_send(
    market_name: str, alert_type: str, storm_id: str, state: dict,
) -> bool:
    """Check if this alert should be sent (deduplication + escalation)."""
    markets = state.get("markets", {})
    prev = markets.get(market_name)
    if prev is None:
        return True

    if prev.get("storm_id") != storm_id:
        return True

    prev_level = _ALERT_LEVELS.get(prev.get("last_alert_type", ""), 0)
    new_level = _ALERT_LEVELS.get(alert_type, 0)
    return new_level > prev_level


def _record_sent(
    market_name: str, alert_type: str, storm_id: str, state: dict,
) -> None:
    """Record that an alert was sent."""
    if "markets" not in state:
        state["markets"] = {}
    state["markets"][market_name] = {
        "last_alert_type": alert_type,
        "storm_id": storm_id,
        "last_alert_time": datetime.now().isoformat(timespec="seconds"),
    }
    state["last_run"] = datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Message formatting — trigger alerts (mrkdwn)
# ---------------------------------------------------------------------------

def _storm_day_label(day: int, scan_date: date) -> str:
    """Human-readable day label like 'Wednesday' or 'Today'."""
    target = scan_date + timedelta(days=day - 1)
    if day == 1:
        return "Today"
    if day == 2:
        return "Tomorrow"
    return target.strftime("%A")


def _format_heads_up(
    mr: MarketResult,
    window: DemandWindow | None,
    alerts: list[NWSAlert],
    scan_date: date,
) -> dict:
    """Format a 'Heads Up' alert (Days 4-8, SLIGHT+)."""
    day_label = _storm_day_label(mr.day, scan_date)
    max_prob = max(mr.max_hail, mr.max_tornado, mr.max_wind)

    lines = [
        f"\u26a0\ufe0f *{mr.market.short_name} Metro — Severe Weather Possible {day_label}*",
        f"{max_prob}% any-severe probability across {mr.affected_counties} of {mr.total_counties} counties.",
    ]
    if window:
        lines.append(f"If confirmed, expect {format_window(window).lower()}.")
    action = f"Action: heads up for {mr.market.owner}" if mr.market.owner else "Action: monitor forecast updates"
    if mr.market.owner:
        action += ", monitor forecast updates."
    else:
        action += "."
    lines.append(action)

    text = "\n".join(lines)
    return {"text": text}


def _format_plan_for_it(
    mr: MarketResult,
    window: DemandWindow | None,
    alerts: list[NWSAlert],
    scan_date: date,
) -> dict:
    """Format a 'Plan For It' alert (Days 1-3, ENH+)."""
    day_label = _storm_day_label(mr.day, scan_date)
    risk_name = risk_display_name(mr.highest_risk)

    lines = [
        f"\U0001f534 *{mr.market.short_name} Metro — Severe Weather Expected {day_label}*",
        f"{risk_name} risk. {mr.affected_counties} counties affected. "
        f"Hail: {mr.max_hail}% | Tornado: {mr.max_tornado}% | Wind: {mr.max_wind}%",
    ]

    # Add NWS alert summary if any
    alert_parts = []
    for a in alerts:
        alert_parts.append(a.event)
    if alert_parts:
        from collections import Counter
        counts = Counter(alert_parts)
        alert_str = ", ".join(f"{n} {evt}" for evt, n in counts.most_common())
        lines.append(f"\u26a0\ufe0f {alert_str} active.")

    if window:
        lines.append(f"Expect {format_window(window).lower()}.")

    states_str = "/".join(mr.market.states)
    if mr.market.owner:
        lines.append(f"Action: flag for {mr.market.owner}, check sub availability in {states_str}.")
    else:
        lines.append(f"Action: check sub availability in {states_str}.")

    text = "\n".join(lines)
    return {"text": text}


def _format_it_happened(
    mr: MarketResult,
    window: DemandWindow | None,
    alerts: list[NWSAlert],
    scan_date: date,
) -> dict:
    """Format an 'It Happened' alert (Day 1 + NWS confirmed)."""
    risk_name = risk_display_name(mr.highest_risk)

    lines = [
        f"\U0001f6a8 *{mr.market.short_name} Metro — Storm Confirmed Today*",
        f"{risk_name} risk confirmed.",
    ]

    # Alert counts
    from collections import Counter
    counts = Counter(a.event for a in alerts)
    if counts:
        parts = [f"{n} {evt}" for evt, n in counts.most_common()]
        lines[-1] += f" {', '.join(parts)} active."

    if window:
        lines.append(f"Volume window: {window.window_start.strftime('%b %-d')}–"
                      f"{window.window_end.strftime('%b %-d')}. Start scheduling crews.")

    if mr.market.owner:
        lines.append(f"Action: {mr.market.owner} to activate {mr.market.name} response plan.")
    else:
        lines.append(f"Action: activate {mr.market.name} response plan.")

    text = "\n".join(lines)
    return {"text": text}


# ---------------------------------------------------------------------------
# Message formatting — daily summary (Block Kit)
# ---------------------------------------------------------------------------

def _format_summary(
    results: list[DayResult],
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    nws_alerts: dict[str, list[NWSAlert]],
    scan_date: date,
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

def post_alerts(
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    nws_alerts: dict[str, list[NWSAlert]],
    webhook_url: str,
    scan_date: date | None = None,
) -> int:
    """Evaluate trigger conditions and post alert messages.

    Returns count of messages sent.
    """
    if scan_date is None:
        scan_date = date.today()

    state = _load_state()
    sent_count = 0

    # Build window lookup
    window_lookup: dict[str, DemandWindow] = {}
    for w in demand_windows:
        window_lookup[w.market.short_name] = w

    # Build NWS alerts lookup by state
    # Flatten to per-market alerts based on market states
    market_alerts: dict[str, list[NWSAlert]] = {}
    for day, mrs in market_results.items():
        for mr in mrs:
            key = mr.market.short_name
            if key not in market_alerts:
                alerts_for_market: list[NWSAlert] = []
                for s in mr.market.states:
                    alerts_for_market.extend(nws_alerts.get(s, []))
                market_alerts[key] = alerts_for_market

    # Evaluate each market-day combination
    for day in sorted(market_results):
        for mr in market_results[day]:
            short = mr.market.short_name
            storm_id = f"{scan_date}-day{day}"
            window = window_lookup.get(short)
            alerts = market_alerts.get(short, [])

            # Determine trigger type
            alert_type = None
            payload = None

            if day == 1 and has_confirmed_warnings(alerts):
                alert_type = "it_happened"
                payload = _format_it_happened(mr, window, alerts, scan_date)
            elif day <= 3 and mr.highest_risk >= 4:  # ENH+
                alert_type = "plan_for_it"
                payload = _format_plan_for_it(mr, window, alerts, scan_date)
            elif day >= 4 and mr.highest_risk >= 3:  # SLIGHT+
                alert_type = "heads_up"
                payload = _format_heads_up(mr, window, alerts, scan_date)

            if alert_type is None:
                continue

            if not _should_send(short, alert_type, storm_id, state):
                print(f"  {short} Day {day}: {alert_type} already sent, skipping",
                      file=sys.stderr)
                continue

            print(f"  {short} Day {day}: sending {alert_type}...", file=sys.stderr)
            if _post_message(webhook_url, payload):
                _record_sent(short, alert_type, storm_id, state)
                sent_count += 1

    _save_state(state)
    return sent_count


def post_summary(
    results: list[DayResult],
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    nws_alerts: dict[str, list[NWSAlert]],
    webhook_url: str,
    scan_date: date | None = None,
) -> bool:
    """Post a full daily summary to Slack. Returns True on success."""
    if scan_date is None:
        scan_date = date.today()
    payload = _format_summary(results, market_results, demand_windows, nws_alerts, scan_date)
    return _post_message(webhook_url, payload)


if __name__ == "__main__":
    import json as _json

    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from markets import classify_markets
    from demand import compute_windows

    print("Running full pipeline → Slack output preview...", file=sys.stderr)

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

    # Preview trigger alerts
    print("\n=== Alert Triggers ===")
    window_lookup = {w.market.short_name: w for w in windows}
    for day in sorted(market_results):
        for mr in market_results[day]:
            short = mr.market.short_name
            alerts = []
            for s in mr.market.states:
                alerts.extend(nws.get(s, []))

            window = window_lookup.get(short)
            if day == 1 and has_confirmed_warnings(alerts):
                p = _format_it_happened(mr, window, alerts, scan_date)
                print(f"\n[IT HAPPENED] {short} Day {day}:")
            elif day <= 3 and mr.highest_risk >= 4:
                p = _format_plan_for_it(mr, window, alerts, scan_date)
                print(f"\n[PLAN FOR IT] {short} Day {day}:")
            elif day >= 4 and mr.highest_risk >= 3:
                p = _format_heads_up(mr, window, alerts, scan_date)
                print(f"\n[HEADS UP] {short} Day {day}:")
            else:
                print(f"\n[NO TRIGGER] {short} Day {day}: risk {mr.highest_risk} (below threshold)")
                continue
            print(p["text"])
