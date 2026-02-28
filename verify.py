"""Verify past forecasts against archived NWS alert snapshots."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from archive import list_recent_runs


@dataclass
class ForecastVerification:
    """Verification of a single market-day forecast against NWS alerts."""
    run_date: str
    market_short: str
    market_name: str
    day: int
    highest_risk: int
    states: list[str]
    had_warnings: bool         # True if archived NWS alerts had confirmed warnings
    hit: bool = False          # True if forecast risk AND warnings both present
    false_alarm: bool = False  # True if forecast risk but NO warnings


@dataclass
class AccuracyReport:
    """Summary accuracy report across multiple archived runs."""
    verifications: list[ForecastVerification] = field(default_factory=list)
    days_analyzed: int = 0

    @property
    def total_forecasts(self) -> int:
        return len(self.verifications)

    @property
    def hits(self) -> int:
        return sum(1 for v in self.verifications if v.hit)

    @property
    def false_alarms(self) -> int:
        return sum(1 for v in self.verifications if v.false_alarm)

    @property
    def hit_rate(self) -> float:
        """Fraction of forecasts that were confirmed by warnings."""
        if not self.verifications:
            return 0.0
        return self.hits / len(self.verifications)

    @property
    def false_alarm_rate(self) -> float:
        """Fraction of forecasts with no confirming warnings."""
        if not self.verifications:
            return 0.0
        return self.false_alarms / len(self.verifications)


def verify_recent_forecasts(days: int = 7) -> AccuracyReport:
    """Read archived runs and verify Day 1 forecasts against NWS alert snapshots.

    Only Day 1 forecasts can be verified since alerts are captured the same day.
    Only markets with risk >= SLIGHT (3) are checked.
    """
    runs = list_recent_runs(days=days)
    report = AccuracyReport(days_analyzed=days)

    for run in runs:
        scan_date = run.get("scan_date", "")
        nws_alerts = run.get("nws_alerts", {})

        # Find Day 1 market results with risk >= SLIGHT
        for mr in run.get("market_results", []):
            if mr.get("day") != 1:
                continue
            if mr.get("highest_risk", 0) < 3:
                continue

            market_short = mr.get("market_short", "")
            market_name = mr.get("market_name", "")
            states = mr.get("states", [])

            # Check if archived NWS alerts had warnings for this market's states
            had_warnings = _check_warnings_for_states(nws_alerts, states)

            verification = ForecastVerification(
                run_date=scan_date,
                market_short=market_short,
                market_name=market_name,
                day=1,
                highest_risk=mr.get("highest_risk", 0),
                states=states,
                had_warnings=had_warnings,
                hit=had_warnings,
                false_alarm=not had_warnings,
            )
            report.verifications.append(verification)

    return report


def _check_warnings_for_states(
    nws_alerts: dict[str, list[dict]],
    states: list[str],
) -> bool:
    """Check if any archived NWS alerts for the given states contain warnings."""
    warning_events = {"Tornado Warning", "Severe Thunderstorm Warning",
                      "Hurricane Warning", "Extreme Wind Warning"}

    for state in states:
        alerts = nws_alerts.get(state, [])
        for alert in alerts:
            event = alert.get("event", "")
            if event in warning_events:
                return True
            # Also check certainty == "Observed"
            if alert.get("certainty") == "Observed":
                return True
    return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify forecast accuracy")
    parser.add_argument("--days", type=int, default=7, help="Days of history to check")
    args = parser.parse_args()

    report = verify_recent_forecasts(days=args.days)

    if not report.verifications:
        print(f"No Day 1 forecasts with risk >= SLIGHT found in last {args.days} day(s).")
        print("Run 'python main.py briefing' or 'python main.py full' to create archives.")
        sys.exit(0)

    print(f"Forecast Accuracy Report (last {args.days} day(s))")
    print(f"{'=' * 50}")
    print(f"  Forecasts checked: {report.total_forecasts}")
    print(f"  Hits (confirmed):  {report.hits}")
    print(f"  False alarms:      {report.false_alarms}")
    print(f"  Hit rate:          {report.hit_rate:.0%}")
    print(f"  False alarm rate:  {report.false_alarm_rate:.0%}")
    print()

    for v in report.verifications:
        status = "HIT" if v.hit else "FALSE ALARM"
        print(f"  {v.run_date} | {v.market_short} | risk={v.highest_risk} | "
              f"warnings={'yes' if v.had_warnings else 'no'} | {status}")
