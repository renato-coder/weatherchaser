"""Archive forecast runs as JSON for history and accuracy tracking."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from config import REMI_MARKETS
from demand import DemandWindow
from markets import MarketResult
from sources.nws_alerts import NWSAlert

_RUNS_DIR = os.path.join(os.path.dirname(__file__), "data", "runs")


def archive_run(
    scan_date: date,
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    nws_alerts: dict[str, list[NWSAlert]],
    data_freshness: str = "",
    briefing_text: str | None = None,
    validation_result: dict | None = None,
) -> str:
    """Write a run snapshot to data/runs/{timestamp}.json. Returns the file path."""
    os.makedirs(_RUNS_DIR, exist_ok=True)

    now = datetime.now()
    filename = now.strftime("%Y%m%dT%H%M%S") + ".json"
    filepath = os.path.join(_RUNS_DIR, filename)

    # Serialize market results
    markets_data: list[dict] = []
    for day in sorted(market_results):
        for mr in market_results[day]:
            markets_data.append({
                "day": mr.day,
                "market_name": mr.market.name,
                "market_short": mr.market.short_name,
                "states": mr.market.states,
                "highest_risk": mr.highest_risk,
                "affected_counties": mr.affected_counties,
                "total_counties": mr.total_counties,
                "max_hail": mr.max_hail,
                "max_tornado": mr.max_tornado,
                "max_wind": mr.max_wind,
                "significant": mr.significant,
            })

    # Serialize demand windows
    windows_data: list[dict] = []
    for w in demand_windows:
        windows_data.append({
            "market_short": w.market.short_name,
            "storm_date": w.storm_date.isoformat(),
            "window_start": w.window_start.isoformat(),
            "window_end": w.window_end.isoformat(),
            "trigger_day": w.trigger_day,
            "highest_risk": w.highest_risk,
        })

    # Serialize NWS alerts
    alerts_data: dict[str, list[dict]] = {}
    for state, alerts in nws_alerts.items():
        alerts_data[state] = [
            {
                "event": a.event,
                "headline": a.headline,
                "severity": a.severity,
                "area_desc": a.area_desc,
                "onset": a.onset,
                "expires": a.expires,
            }
            for a in alerts
        ]

    run_data = {
        "run_timestamp": now.isoformat(),
        "scan_date": scan_date.isoformat(),
        "data_freshness": data_freshness,
        "market_results": markets_data,
        "demand_windows": windows_data,
        "nws_alerts": alerts_data,
        "briefing_text": briefing_text,
        "validation": validation_result,
    }

    with open(filepath, "w") as f:
        json.dump(run_data, f, indent=2)

    print(f"  Archived run to {filepath}", file=sys.stderr)
    return filepath


def list_recent_runs(days: int = 7) -> list[dict]:
    """Read archived run JSON files from the last N days."""
    if not os.path.isdir(_RUNS_DIR):
        return []

    cutoff = datetime.now() - timedelta(days=days)
    runs: list[dict] = []

    for filename in sorted(os.listdir(_RUNS_DIR)):
        if not filename.endswith(".json"):
            continue

        # Parse timestamp from filename: YYYYMMDDTHHMMSS.json
        stem = filename.removesuffix(".json")
        try:
            file_dt = datetime.strptime(stem, "%Y%m%dT%H%M%S")
        except ValueError:
            continue

        if file_dt < cutoff:
            continue

        filepath = os.path.join(_RUNS_DIR, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
            data["_filename"] = filename
            runs.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return runs


if __name__ == "__main__":
    runs = list_recent_runs(days=30)
    if not runs:
        print("No archived runs found.")
    else:
        print(f"Found {len(runs)} archived run(s):")
        for r in runs:
            ts = r.get("run_timestamp", "?")
            n_markets = len(r.get("market_results", []))
            has_briefing = "yes" if r.get("briefing_text") else "no"
            print(f"  {r.get('_filename', '?')} — {n_markets} market results, "
                  f"briefing: {has_briefing}")
