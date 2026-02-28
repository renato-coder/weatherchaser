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

    # briefing command
    briefing_parser = subparsers.add_parser(
        "briefing", help="AI-generated demand briefing for Slack",
    )
    briefing_parser.add_argument("--states", help="Filter states")
    briefing_parser.add_argument(
        "--slack", action="store_true",
        help="Post briefing to Slack",
    )

    # verify command
    verify_parser = subparsers.add_parser(
        "verify", help="Verify past forecast accuracy against archived NWS alerts",
    )
    verify_parser.add_argument(
        "--days", type=int, default=7,
        help="Number of days of history to check (default: 7)",
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
    elif args.command == "briefing":
        _cmd_briefing(args)
    elif args.command == "verify":
        _cmd_verify(args)
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
    from demand import format_window
    from classifier import risk_display_name
    from sources.nws_alerts import fetch_alerts_for_states

    results, market_results, windows, any_data, data_freshness = _run_pipeline(
        states=getattr(args, "states", None),
    )

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

    if data_freshness:
        print(f"\n  Data as of {data_freshness}")

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

    # Slack: post daily summary
    if getattr(args, "slack", False):
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            print("  Warning: SLACK_WEBHOOK_URL not set, skipping Slack", file=sys.stderr)
        else:
            from output.slack import post_summary
            ok = post_summary(results, market_results, windows, nws_alerts, webhook,
                              data_freshness=data_freshness)
            if ok:
                print("  Slack summary posted.", file=sys.stderr)

    # Archive this run
    from archive import archive_run
    archive_run(
        scan_date=date.today(),
        market_results=market_results,
        demand_windows=windows,
        nws_alerts=nws_alerts,
        data_freshness=data_freshness,
    )


def _cmd_briefing(args: argparse.Namespace) -> None:
    """AI-generated demand briefing using Claude Sonnet."""
    from config import BRIEFING_CATEGORICAL_MIN, BRIEFING_MAX_DAY
    from output.briefing import (
        generate_briefing, post_briefing, prepare_briefing_data, validate_briefing,
    )

    # Check for API key early
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set. Add it to .env or environment.",
              file=sys.stderr)
        sys.exit(1)

    results, market_results, windows, any_data, data_freshness = _run_pipeline(
        states=getattr(args, "states", None),
        categorical_min=BRIEFING_CATEGORICAL_MIN,
    )

    # Filter to Days 1-5 only
    results = [dr for dr in results if dr.day <= BRIEFING_MAX_DAY]
    market_results = {d: mrs for d, mrs in market_results.items() if d <= BRIEFING_MAX_DAY}
    windows = [w for w in windows if w.trigger_day <= BRIEFING_MAX_DAY]

    scan_date = date.today()
    data = prepare_briefing_data(market_results, windows, scan_date,
                                 data_freshness=data_freshness)

    print("\nGenerating briefing...", file=sys.stderr)
    text = generate_briefing(data)
    if text is None:
        print("Error: Failed to generate briefing.", file=sys.stderr)
        sys.exit(1)

    # Validate briefing against input data
    validation = validate_briefing(text, data)
    if not validation.passed:
        for err in validation.errors:
            print(f"  VALIDATION ERROR: {err}", file=sys.stderr)
    for warn in validation.warnings:
        print(f"  VALIDATION WARNING: {warn}", file=sys.stderr)
    if validation.passed and not validation.warnings:
        print("  Validation passed.", file=sys.stderr)

    # Always print to stdout
    print(text)

    if getattr(args, "slack", False):
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
        if not webhook:
            print("  Warning: SLACK_WEBHOOK_URL not set, skipping Slack", file=sys.stderr)
        elif post_briefing(text, webhook):
            print("  Briefing posted to Slack.", file=sys.stderr)

    # Archive this run
    from archive import archive_run
    # Fetch NWS alerts for archiving (needed for verify command)
    from sources.nws_alerts import fetch_alerts_for_states
    from config import REMI_MARKETS

    alert_states: set[str] = set()
    for m in REMI_MARKETS:
        alert_states.update(m.states)
    print("Fetching NWS alerts for archive...", file=sys.stderr)
    nws_alerts = fetch_alerts_for_states(sorted(alert_states))

    validation_dict = {
        "passed": validation.passed,
        "warnings": validation.warnings,
        "errors": validation.errors,
    }
    archive_run(
        scan_date=scan_date,
        market_results=market_results,
        demand_windows=windows,
        nws_alerts=nws_alerts,
        data_freshness=data_freshness,
        briefing_text=text,
        validation_result=validation_dict,
    )


def _cmd_verify(args: argparse.Namespace) -> None:
    """Verify past forecast accuracy against archived NWS alerts."""
    from verify import verify_recent_forecasts
    from classifier import risk_display_name

    days = getattr(args, "days", 7)
    report = verify_recent_forecasts(days=days)

    if not report.verifications:
        print(f"No Day 1 forecasts with risk >= SLIGHT found in last {days} day(s).")
        print("Run 'python main.py briefing' or 'python main.py full' to create archives.")
        return

    print(f"Forecast Accuracy Report (last {days} day(s))")
    print(f"{'=' * 50}")
    print(f"  Forecasts checked: {report.total_forecasts}")
    print(f"  Hits (confirmed):  {report.hits}")
    print(f"  False alarms:      {report.false_alarms}")
    print(f"  Hit rate:          {report.hit_rate:.0%}")
    print(f"  False alarm rate:  {report.false_alarm_rate:.0%}")
    print()

    for v in report.verifications:
        status = "HIT" if v.hit else "FALSE ALARM"
        risk = risk_display_name(v.highest_risk)
        print(f"  {v.run_date} | {v.market_short} | {risk} | "
              f"warnings={'yes' if v.had_warnings else 'no'} | {status}")


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def run_scan(states: str | None = None) -> tuple[list, bool]:
    """Run the full SPC scan pipeline. Returns (results, data_available)."""
    results, _, _, any_data, _ = _run_pipeline(states=states)
    return results, any_data


def _run_pipeline(
    states: str | None = None,
    categorical_min: int | None = None,
) -> tuple[list, dict, list, bool, str]:
    """Run scan pipeline. Returns (results, market_results, windows, any_data, data_freshness)."""
    from sources.spc import fetch_spc_outlooks, get_fetch_metadata
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from markets import classify_markets
    from demand import compute_windows

    print("Fetching SPC outlooks...", file=sys.stderr)
    outlooks, any_data = fetch_spc_outlooks()

    # Compute human-readable freshness string from SPC response headers
    data_freshness = _compute_freshness(get_fetch_metadata())

    print("Loading county boundaries...", file=sys.stderr)
    counties = load_counties()

    if states:
        state_filter = {s.strip().upper() for s in states.split(",")}
        counties = [c for c in counties if c.state_abbr in state_filter]
        print(f"  Filtering to {len(counties)} counties in {state_filter}", file=sys.stderr)

    print("Matching counties to risk areas...", file=sys.stderr)
    matched = match_counties(outlooks, counties)

    print("Classifying results...", file=sys.stderr)
    results = classify(matched, data_available=any_data, categorical_min=categorical_min)

    market_results = classify_markets(results)
    windows = compute_windows(market_results)

    return results, market_results, windows, any_data, data_freshness


def _compute_freshness(metadata: dict[str, str]) -> str:
    """Compute a human-readable freshness string from SPC HTTP headers."""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime

    if not metadata:
        return ""

    # Find the most recent Last-Modified timestamp across all fetched URLs
    latest: datetime | None = None
    for header_val in metadata.values():
        try:
            dt = parsedate_to_datetime(header_val)
            if latest is None or dt > latest:
                latest = dt
        except (ValueError, TypeError):
            continue

    if latest is None:
        return ""

    now = datetime.now(timezone.utc)
    delta = now - latest
    hours = int(delta.total_seconds() / 3600)
    minutes = int((delta.total_seconds() % 3600) / 60)

    if hours > 0:
        freshness = f"{hours}h {minutes}m ago"
    else:
        freshness = f"{minutes}m ago"

    time_str = latest.strftime("%H:%M UTC")
    return f"{time_str} ({freshness})"


if __name__ == "__main__":
    main()
