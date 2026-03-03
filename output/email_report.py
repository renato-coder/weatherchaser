"""Email output — HTML formatting for storm briefing emails."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from classifier import risk_display_name
from config import REMI_MARKETS, DayResult
from demand import DemandWindow, format_window
from markets import MarketResult

# Risk level -> emoji for HTML
_RISK_EMOJI: dict[int, str] = {
    6: "\U0001f534",  # HIGH
    5: "\U0001f534",  # MODERATE
    4: "\U0001f7e0",  # ENHANCED
    3: "\U0001f7e1",  # SLIGHT
    2: "\U0001f535",  # MARGINAL
    1: "\u26aa",      # TSTM
    0: "\U0001f7e2",  # NONE
}

# Risk levels that warrant a highlighted row background
_HIGHLIGHT_MIN = 2


def format_briefing_html(
    briefing_text: str,
    results: list[DayResult],
    market_results: dict[int, list[MarketResult]],
    windows: list[DemandWindow],
    data_freshness: str,
    scan_date: date | None = None,
    validation_passed: bool = True,
) -> str:
    """Generate styled HTML email matching the demo format."""
    if scan_date is None:
        scan_date = date.today()

    date_display = scan_date.strftime("%b %d, %Y")
    day_name = scan_date.strftime("%A %B %-d")

    # Build window lookup
    window_lookup: dict[str, DemandWindow] = {}
    for w in windows:
        window_lookup[w.market.short_name] = w

    # Flatten market risk levels across days
    market_best: dict[str, int] = {}
    for day, mrs in market_results.items():
        for mr in mrs:
            key = mr.market.short_name
            market_best[key] = max(market_best.get(key, 0), mr.highest_risk)

    # --- Briefing text as HTML paragraphs ---
    briefing_html = ""
    for line in briefing_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip the header line — already in the HTML template
        if line.startswith("\U0001f4cb Storm Brief") or line.startswith("📋 Storm Brief"):
            continue
        line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
        # Convert *italic* markdown
        line = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', line)
        briefing_html += f"<p>{line}</p>\n"

    # --- 8-Day Outlook table rows ---
    outlook_rows = ""
    for dr in results:
        day = dr.day
        day_date = scan_date + timedelta(days=day - 1)
        date_str = day_date.strftime("%a %b %-d")

        if dr.county_risks:
            states: dict[str, int] = {}
            max_hail = max_torn = max_wind = 0
            highest_level = 0
            for cr in dr.county_risks:
                s = cr.county.state_abbr
                states[s] = states.get(s, 0) + 1
                max_hail = max(max_hail, cr.hail_prob)
                max_torn = max(max_torn, cr.tornado_prob)
                max_wind = max(max_wind, cr.wind_prob)
                highest_level = max(highest_level, cr.categorical_level)

            emoji = _RISK_EMOJI.get(highest_level, "\u26aa")
            state_str = "/".join(sorted(states))
            count = len(dr.county_risks)

            probs = []
            if max_hail:
                probs.append(f"Hail {max_hail}%")
            if max_torn:
                probs.append(f"Tornado {max_torn}%")
            if max_wind:
                probs.append(f"Wind {max_wind}%")
            prob_str = f" &mdash; {' &middot; '.join(probs)}" if probs else ""

            risk_cell = f"{emoji} {count} counties ({state_str}){prob_str}"
            row_bg = ' style="background: #fff3e0;"' if highest_level >= _HIGHLIGHT_MIN else ""
        else:
            risk_cell = "\u26aa No significant risk"
            row_bg = f' style="background: #f9f9f9;"' if day % 2 == 0 else ""

        outlook_rows += (
            f'  <tr{row_bg}>'
            f'<td style="padding: 8px;">Day {day}</td>'
            f'<td style="padding: 8px;">{date_str}</td>'
            f'<td style="padding: 8px;">{risk_cell}</td>'
            f'</tr>\n'
        )

    # --- Markets table rows ---
    market_rows = ""
    for i, market in enumerate(REMI_MARKETS):
        risk_level = market_best.get(market.short_name, 0)
        emoji = _RISK_EMOJI.get(risk_level, "\U0001f7e2")
        status = risk_display_name(risk_level) if risk_level > 0 else "Clear"

        w = window_lookup.get(market.short_name)
        window_str = format_window(w).replace("Volume bump ", "") if w else "\u2014"

        row_bg = ' style="background: #f9f9f9;"' if i % 2 == 1 else ""
        market_rows += (
            f'  <tr{row_bg}>'
            f'<td style="padding: 8px;">{market.name}</td>'
            f'<td style="padding: 8px;">{emoji} {status}</td>'
            f'<td style="padding: 8px;">{window_str}</td>'
            f'</tr>\n'
        )

    validation_str = "\u2705 Passed" if validation_passed else "\u26a0\ufe0f Warnings"
    freshness_str = f"Data as of {data_freshness}" if data_freshness else "Data freshness unavailable"
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    html = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 640px; margin: 0 auto; padding: 20px; color: #1a1a1a;">

<div style="background: #1a1a2e; color: white; padding: 20px 24px; border-radius: 8px 8px 0 0;">
  <h1 style="margin: 0; font-size: 20px;">\U0001f329\ufe0f REMI CAT TRACKER</h1>
  <p style="margin: 6px 0 0; font-size: 13px; color: #a0a0b0;">{date_display} &mdash; Automated Storm Intelligence</p>
</div>

<div style="border: 1px solid #e0e0e0; border-top: none; border-radius: 0 0 8px 8px; padding: 24px;">

<h2 style="font-size: 16px; margin-top: 0;">\U0001f4cb Storm Brief &mdash; {day_name}</h2>

{briefing_html}

<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;">

<h2 style="font-size: 16px;">\U0001f4c5 8-Day Outlook</h2>

<table style="width: 100%; border-collapse: collapse; font-size: 14px;">
  <tr style="background: #f5f5f5;">
    <th style="text-align: left; padding: 8px;">Day</th>
    <th style="text-align: left; padding: 8px;">Date</th>
    <th style="text-align: left; padding: 8px;">Risk</th>
  </tr>
{outlook_rows}</table>

<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;">

<h2 style="font-size: 16px;">\U0001f3e2 Remi Markets</h2>

<table style="width: 100%; border-collapse: collapse; font-size: 14px;">
  <tr style="background: #f5f5f5;">
    <th style="text-align: left; padding: 8px;">Market</th>
    <th style="text-align: left; padding: 8px;">Status</th>
    <th style="text-align: left; padding: 8px;">Demand Window</th>
  </tr>
{market_rows}</table>

<hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;">

<div style="font-size: 12px; color: #888;">
  <p>\U0001f552 {freshness_str} &middot; SPC Convective Outlooks + NWS Active Alerts<br>
  AI briefing generated by Claude Sonnet &middot; Validation: {validation_str}<br>
  Run archived: {timestamp}</p>
  <p style="margin-top: 12px;">This report is generated automatically by the <strong>REMI CAT Event Tracker</strong>. It scans NOAA/SPC convective outlooks, matches risk polygons against county boundaries for all 10 Remi markets, and projects demand windows 14-28 days post-storm.</p>
</div>

</div>
</body>
</html>"""

    return html
