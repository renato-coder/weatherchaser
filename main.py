"""Remi CAT Event Tracker — CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Remi CAT Event Tracker — track severe weather risk across US counties",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress stderr progress messages (useful for cron)",
    )
    subparsers = parser.add_subparsers(dest="command")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan SPC convective outlooks")
    scan_parser.add_argument(
        "--states",
        help="Comma-separated state codes to filter (e.g. TX,OK,CO)",
    )
    scan_parser.add_argument(
        "--slack", action="store_true",
        help="Post summary to Slack",
    )
    scan_parser.add_argument(
        "--csv",
        metavar="PATH",
        help="Export results to CSV file",
    )

    # markets command
    markets_parser = subparsers.add_parser(
        "markets", help="Scan + market classification + demand windows",
    )
    markets_parser.add_argument("--states", help="Filter states")
    markets_parser.add_argument("--slack", action="store_true", help="Post summary to Slack")
    markets_parser.add_argument("--csv", metavar="PATH", help="Export to CSV")

    # alerts command
    alerts_parser = subparsers.add_parser(
        "alerts", help="Fetch NWS active alerts for market states",
    )

    # full command
    full_parser = subparsers.add_parser(
        "full", help="Full pipeline: scan + markets + alerts + all outputs",
    )
    full_parser.add_argument("--states", help="Filter states")
    full_parser.add_argument(
        "--slack", action="store_true",
        help="Post alert triggers to Slack (escalation-aware)",
    )
    full_parser.add_argument("--csv", metavar="PATH", help="Export to CSV")
    full_parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress stderr progress messages",
    )

    args = parser.parse_args()

    # Redirect stderr to /dev/null in quiet mode
    if getattr(args, "quiet", False):
        sys.stderr = open(os.devnull, "w")

    if args.command == "scan":
        _cmd_scan(args)
    elif args.command == "markets":
        _cmd_markets(args)
    elif args.command == "alerts":
        _cmd_alerts(args)
    elif args.command == "full":
        _cmd_full(args)
    else:
        parser.print_help()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_scan(args: argparse.Namespace) -> None:
    """Scan SPC outlooks and render results."""
    from output.console import render_console

    results, any_data = run_scan(states=getattr(args, "states", None))
    print(file=sys.stderr)
    render_console(results, data_available=any_data)

    if getattr(args, "csv", None):
        from markets import classify_markets
        from demand import compute_windows
        from output.csv_export import export_csv

        market_results = classify_markets(results)
        windows = compute_windows(market_results)
        export_csv(args.csv, results, market_results, windows)

    if getattr(args, "slack", False):
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            print("  Warning: SLACK_WEBHOOK_URL not set, skipping Slack", file=sys.stderr)
            return

        from markets import classify_markets
        from demand import compute_windows
        from output.slack import post_summary

        market_results = classify_markets(results)
        windows = compute_windows(market_results)
        ok = post_summary(results, market_results, windows, {}, webhook)
        if ok:
            print("  Slack summary posted.", file=sys.stderr)


def _cmd_markets(args: argparse.Namespace) -> None:
    """Scan + market classification + demand windows."""
    from output.console import render_console
    from markets import classify_markets
    from demand import compute_windows, format_window
    from classifier import risk_display_name

    results, any_data = run_scan(states=getattr(args, "states", None))
    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    print(file=sys.stderr)
    render_console(results, data_available=any_data)

    # Print market summary
    print("\n  REMI MARKETS")
    if not any(mrs for mrs in market_results.values()):
        print("  No Remi markets at CAT-level risk.")
    else:
        for day in sorted(market_results):
            mrs = market_results[day]
            if not mrs:
                continue
            print(f"\n  Day {day}:")
            for mr in sorted(mrs, key=lambda m: -m.highest_risk):
                risk = risk_display_name(mr.highest_risk)
                print(f"    {mr.market.short_name}: {risk} — "
                      f"{mr.affected_counties}/{mr.total_counties} counties "
                      f"(hail:{mr.max_hail}% torn:{mr.max_tornado}% wind:{mr.max_wind}%)")

    if windows:
        print("\n  DEMAND WINDOWS")
        for w in windows:
            print(f"    {w.market.short_name}: {format_window(w)} "
                  f"(storm Day {w.trigger_day}, {w.storm_date})")

    if getattr(args, "csv", None):
        from output.csv_export import export_csv
        export_csv(args.csv, results, market_results, windows)

    if getattr(args, "slack", False):
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            print("  Warning: SLACK_WEBHOOK_URL not set, skipping Slack", file=sys.stderr)
            return
        from output.slack import post_summary
        ok = post_summary(results, market_results, windows, {}, webhook)
        if ok:
            print("  Slack summary posted.", file=sys.stderr)


def _cmd_alerts(args: argparse.Namespace) -> None:
    """Fetch NWS active alerts for states with configured markets."""
    from config import REMI_MARKETS
    from sources.nws_alerts import fetch_alerts_for_states, summarize_alerts

    # Collect unique states from all markets
    market_states: set[str] = set()
    for m in REMI_MARKETS:
        market_states.update(m.states)

    print(f"Fetching NWS alerts for {', '.join(sorted(market_states))}...",
          file=sys.stderr)
    alerts = fetch_alerts_for_states(sorted(market_states))
    summaries = summarize_alerts(alerts)

    print(file=sys.stderr)

    total = 0
    for state in sorted(summaries):
        counts = summaries[state]
        if not counts:
            print(f"  {state}: no relevant alerts")
            continue
        total += sum(counts.values())
        parts = [f"{evt}: {n}" for evt, n in sorted(counts.items())]
        print(f"  {state}: {', '.join(parts)}")

    print(f"\n  Total: {total} relevant alert(s)")


def _cmd_full(args: argparse.Namespace) -> None:
    """Full pipeline: scan + markets + alerts + all outputs."""
    from output.console import render_console
    from markets import classify_markets
    from demand import compute_windows, format_window
    from classifier import risk_display_name
    from sources.nws_alerts import fetch_alerts_for_states

    results, any_data = run_scan(states=getattr(args, "states", None))
    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    # Collect states from markets with risk for NWS alerts
    risk_states: set[str] = set()
    for day, mrs in market_results.items():
        for mr in mrs:
            risk_states.update(mr.market.states)

    # Also add states from all markets for comprehensive alert coverage
    from config import REMI_MARKETS
    for m in REMI_MARKETS:
        risk_states.update(m.states)

    print("Fetching NWS alerts...", file=sys.stderr)
    nws_alerts = fetch_alerts_for_states(sorted(risk_states))

    print(file=sys.stderr)
    render_console(results, data_available=any_data)

    # Market summary
    print("\n  REMI MARKETS")
    if not any(mrs for mrs in market_results.values()):
        print("  No Remi markets at CAT-level risk.")
    else:
        for day in sorted(market_results):
            mrs = market_results[day]
            if not mrs:
                continue
            print(f"\n  Day {day}:")
            for mr in sorted(mrs, key=lambda m: -m.highest_risk):
                risk = risk_display_name(mr.highest_risk)
                print(f"    {mr.market.short_name}: {risk} — "
                      f"{mr.affected_counties}/{mr.total_counties} counties")

    if windows:
        print("\n  DEMAND WINDOWS")
        for w in windows:
            print(f"    {w.market.short_name}: {format_window(w)} "
                  f"(storm Day {w.trigger_day}, {w.storm_date})")

    # CSV export
    if getattr(args, "csv", None):
        from output.csv_export import export_csv
        export_csv(args.csv, results, market_results, windows)

    # Slack: post alert triggers (not summary)
    if getattr(args, "slack", False):
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            print("  Warning: SLACK_WEBHOOK_URL not set, skipping Slack", file=sys.stderr)
            return

        from output.slack import post_alerts
        sent = post_alerts(market_results, windows, nws_alerts, webhook)
        print(f"  {sent} Slack alert(s) sent.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def run_scan(states: str | None = None) -> tuple[list, bool]:
    """Run the full SPC scan pipeline. Returns (results, data_available)."""
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify

    print("Fetching SPC outlooks...", file=sys.stderr)
    outlooks, any_data = fetch_spc_outlooks()

    print("Loading county boundaries...", file=sys.stderr)
    counties = load_counties()

    if states:
        state_filter = {s.strip().upper() for s in states.split(",")}
        counties = [c for c in counties if c.state_abbr in state_filter]
        print(f"  Filtering to {len(counties)} counties in {state_filter}", file=sys.stderr)

    print("Matching counties to risk areas...", file=sys.stderr)
    matched = match_counties(outlooks, counties)

    print("Classifying results...", file=sys.stderr)
    results = classify(matched, data_available=any_data)

    return results, any_data


if __name__ == "__main__":
    main()
