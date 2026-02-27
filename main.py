"""Remi CAT Event Tracker — CLI entrypoint."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Remi CAT Event Tracker — track severe weather risk across US counties",
    )
    subparsers = parser.add_subparsers(dest="command")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan SPC convective outlooks")
    scan_parser.add_argument(
        "--states",
        help="Comma-separated state codes to filter (e.g. TX,OK,CO)",
    )

    args = parser.parse_args()

    if args.command == "scan":
        from output.console import render_console

        results, any_data = run_scan(states=args.states)
        print(file=sys.stderr)
        render_console(results, data_available=any_data)
    else:
        parser.print_help()


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
