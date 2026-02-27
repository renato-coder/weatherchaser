"""Fetch active NWS alerts for states with severe weather risk."""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from dataclasses import dataclass

import requests

from config import (
    HTTP_TIMEOUT,
    NWS_BASE_URL,
    NWS_RELEVANT_EVENTS,
    NWS_USER_AGENT,
    RETRY_DELAY,
)


@dataclass
class NWSAlert:
    """A single active NWS alert."""
    event: str              # "Tornado Warning"
    headline: str | None
    severity: str           # "Extreme", "Severe", "Moderate"
    urgency: str            # "Immediate", "Expected", "Future"
    certainty: str          # "Observed", "Likely", "Possible"
    area_desc: str
    onset: str | None       # ISO 8601
    expires: str | None     # ISO 8601


def fetch_alerts_for_states(
    states: list[str],
) -> dict[str, list[NWSAlert]]:
    """Fetch active NWS alerts for the given state codes.

    Returns state_abbr -> list of relevant NWSAlert.
    """
    headers = {
        "User-Agent": NWS_USER_AGENT,
        "Accept": "application/geo+json",
    }

    results: dict[str, list[NWSAlert]] = {}

    for i, state in enumerate(states):
        if i > 0:
            time.sleep(0.5)  # Pace requests per NWS guidelines

        url = (
            f"{NWS_BASE_URL}/alerts/active"
            f"?area={state}&status=actual&message_type=alert,update"
        )

        alerts: list[NWSAlert] = []
        for attempt in range(2):
            try:
                resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                if attempt == 0:
                    print(f"  NWS retry for {state}: {exc}", file=sys.stderr)
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"  NWS failed for {state}: {exc}", file=sys.stderr)
                    results[state] = []
                    continue
        else:
            results[state] = []
            continue

        for feature in data.get("features", []):
            props = feature.get("properties", {})
            event = props.get("event", "")

            if event not in NWS_RELEVANT_EVENTS:
                continue

            alert = NWSAlert(
                event=event,
                headline=props.get("headline"),
                severity=props.get("severity", ""),
                urgency=props.get("urgency", ""),
                certainty=props.get("certainty", ""),
                area_desc=props.get("areaDesc", ""),
                onset=props.get("onset"),
                expires=props.get("expires"),
            )
            alerts.append(alert)

        results[state] = alerts
        print(f"  {state}: {len(alerts)} relevant alert(s)", file=sys.stderr)

    return results


def summarize_alerts(
    alerts: dict[str, list[NWSAlert]],
) -> dict[str, dict[str, int]]:
    """Group alerts by state and event type.

    Returns {"TX": {"Tornado Warning": 2, "Severe Thunderstorm Watch": 1}}.
    """
    summaries: dict[str, dict[str, int]] = {}
    for state, state_alerts in alerts.items():
        counts: dict[str, int] = defaultdict(int)
        for alert in state_alerts:
            counts[alert.event] += 1
        summaries[state] = dict(counts)
    return summaries


def has_confirmed_warnings(alerts: list[NWSAlert]) -> bool:
    """Check if any alerts indicate confirmed severe weather.

    Returns True if any alert has certainty=="Observed" or is a Warning
    (not Watch) event type — used as "It Happened" proxy.
    """
    for alert in alerts:
        if alert.certainty == "Observed":
            return True
        if "Warning" in alert.event:
            return True
    return False


if __name__ == "__main__":
    test_states = ["TX", "OK"]
    print(f"Fetching NWS alerts for {', '.join(test_states)}...", file=sys.stderr)

    alerts = fetch_alerts_for_states(test_states)
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

        # Check for confirmed warnings
        if has_confirmed_warnings(alerts[state]):
            print(f"    ↳ Confirmed warnings present (\"It Happened\" trigger)")

    print(f"\n  Total: {total} relevant alert(s) across {len(test_states)} state(s)")
