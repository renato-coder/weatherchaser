---
title: "feat: Alert-based Slack Notifications with Demand Framing (Steps 6-10)"
type: feat
status: completed
date: 2026-02-27
---

# Alert-based Slack Notifications with Demand Framing (Steps 6-10)

## Overview

Extend the Remi CAT Event Tracker from a console-only scan tool into an operational alert system the team actually uses. The core pipeline (Steps 1-5) produces classified county-level risk data. This plan adds: market definitions mapping 10 metro areas to county FIPS codes, a demand window calculator showing when roofing volume will spike, alert-triggered Slack notifications with conversational tone, NWS active alert enrichment, CSV export, and full CLI orchestration.

Key architectural decisions:
- **Simple state file** (`data/alert_state.json`) for Slack deduplication across cron runs
- **NWS confirmed warnings as proxy** for "It Happened" trigger (no SPC storm report parsing needed for MVP)
- **Manually curated FIPS lists** per market in `config.py`

## Problem Statement

The scan tool works but nobody uses it. The team needs:
1. **Market-level answers** — "Is DFW at risk?" not "Is FIPS 48113 at risk?"
2. **Timing context** — "When will demand spike?" not just "storm on Wednesday"
3. **Push notifications** — Alerts that come to them in Slack, not a CLI they have to remember to run
4. **Actionable framing** — "Flag for Bryan, check sub availability" not raw weather data

## Proposed Solution

### Data Flow (Extended Pipeline)

```
main.py (CLI orchestrator)
  → sources/spc.fetch_spc_outlooks()        → dict[int, list[RiskPolygon]]
  → geo/counties.load_counties()            → list[County]
  → geo/matcher.match_counties()            → dict[int, list[CountyRisk]]
  → classifier.classify()                   → list[DayResult]
  → markets.classify_markets()              → list[MarketResult]      ← NEW
  → demand.compute_windows()                → list[DemandWindow]      ← NEW
  → sources/nws_alerts.fetch_alerts()       → dict[str, list[NWSAlert]] ← NEW
  → output/slack.post_alerts()              → Slack messages           ← NEW
  → output/csv_export.export()              → CSV file                 ← NEW
  → output/console.render_console()         → terminal (updated)
```

### New Data Model

```python
# config.py additions

@dataclass
class Market:
    name: str           # "Dallas-Fort Worth"
    short_name: str     # "DFW"
    fips_codes: list[str]  # ["48085", "48113", ...]
    states: list[str]   # ["TX"]
    owner: str          # "Bryan"  — person to flag in Slack

REMI_MARKETS: list[Market] = [...]  # 10 markets

DEMAND_WINDOW_START_DAYS = 14  # Days after storm before demand rises
DEMAND_WINDOW_END_DAYS = 28    # Days after storm when demand peaks end

# NWS constants
NWS_BASE_URL = "https://api.weather.gov"
NWS_USER_AGENT = "(remi-cat-tracker, contact@remirc.com)"
NWS_RELEVANT_EVENTS: set[str] = {
    "Tornado Warning", "Tornado Watch",
    "Severe Thunderstorm Warning", "Severe Thunderstorm Watch",
    "Hurricane Warning", "Hurricane Watch",
    "Extreme Wind Warning",
}
```

```python
# markets.py — new module

@dataclass
class MarketResult:
    market: Market
    day: int
    highest_risk: int           # Max categorical_level across market counties
    affected_counties: int      # Count of counties meeting threshold
    total_counties: int         # Total counties in market
    max_hail: int
    max_tornado: int
    max_wind: int
    significant: bool
    county_risks: list[CountyRisk]  # The actual flagged counties
```

```python
# demand.py — new module

@dataclass
class DemandWindow:
    storm_date: date
    window_start: date   # storm_date + 14 days
    window_end: date     # storm_date + 28 days
    market: Market
    trigger_day: int     # SPC day number (1-8) or 0 for confirmed
    confirmed: bool      # True if storm reports / NWS confirmed
```

```python
# sources/nws_alerts.py — new module

@dataclass
class NWSAlert:
    event: str           # "Tornado Warning"
    headline: str | None
    severity: str        # "Extreme", "Severe", "Moderate"
    urgency: str         # "Immediate", "Expected", "Future"
    certainty: str       # "Observed", "Likely", "Possible"
    area_desc: str
    onset: str | None    # ISO 8601
    expires: str | None  # ISO 8601
```

```python
# Alert state tracking — data/alert_state.json

{
    "last_run": "2026-02-27T14:00:00",
    "markets": {
        "DFW": {
            "last_alert_type": "heads_up",
            "last_alert_day": 6,
            "last_alert_time": "2026-02-27T10:00:00",
            "storm_id": "2026-02-27-day6"
        }
    }
}
```

## Technical Approach

### Architecture

Seven new/modified files, built in dependency order:

| File | Role | Depends On |
|------|------|-----------|
| `config.py` (modify) | Market dataclass, FIPS lists, NWS constants, demand window params | — |
| `markets.py` (new) | Join DayResult to markets, produce MarketResult | config.py |
| `demand.py` (new) | Compute demand windows from MarketResult | config.py |
| `sources/nws_alerts.py` (new) | Fetch NWS active alerts by state | config.py |
| `output/slack.py` (new) | Format + post Slack messages, manage alert state | markets.py, demand.py, nws_alerts.py |
| `output/csv_export.py` (new) | Export to CSV with demand window columns | markets.py, demand.py |
| `main.py` (modify) | Wire CLI commands + flags | all above |

### Implementation Phases

#### Phase 0: Prerequisites (fix existing issues)

Before building on the pipeline, resolve two blockers from the code review:

- [x] **Fix `run_scan()` to return results** (todo #009) — currently renders directly, must return `(results, data_available)` so CLI can route to Slack/CSV
- [x] **Fix probabilistic merge asymmetry** (todo #005) — Day 4-8 "any severe" prob should also set `tornado_prob` for consistent threshold checks

#### Phase 1: Market Definitions (`config.py`, `markets.py`)

- [x] Replace `REMI_MARKETS` plain dicts with `Market` dataclass in `config.py`
- [x] Add `Market` dataclass: `name`, `short_name`, `fips_codes`, `states`, `owner`
- [x] Curate FIPS codes for 10 markets:
  - [x] Dallas-Fort Worth (DFW) — ~12 counties: Tarrant 48439, Dallas 48113, Collin 48085, Denton 48121, Ellis 48139, Johnson 48251, Kaufman 48257, Parker 48367, Rockwall 48397, Hunt 48231, Wise 48497, Hood 48221
  - [x] Houston — ~9 counties: Harris 48201, Fort Bend 48157, Montgomery 48339, Brazoria 48039, Galveston 48167, Liberty 48291, Chambers 48071, Waller 48473, Austin 48015
  - [x] Oklahoma City (OKC) — ~7 counties: Oklahoma 40109, Cleveland 40027, Canadian 40017, Grady 40051, Logan 40083, McClain 40087, Lincoln 40081
  - [x] Denver — ~7 counties: Denver 08031, Arapahoe 08005, Jefferson 08059, Adams 08001, Douglas 08035, Broomfield 08014, Boulder 08013
  - [x] Nashville — ~7 counties: Davidson 47037, Williamson 47187, Rutherford 47149, Sumner 47165, Wilson 47189, Robertson 47147, Cheatham 47021
  - [x] San Antonio — ~5 counties: Bexar 48029, Comal 48091, Guadalupe 48187, Medina 48325, Kendall 48259
  - [x] Minneapolis — ~7 counties: Hennepin 27053, Ramsey 27123, Dakota 27037, Anoka 27003, Washington 27163, Scott 27139, Carver 27019
  - [x] Atlanta — ~10 counties: Fulton 13121, DeKalb 13089, Gwinnett 13135, Cobb 13067, Clayton 13063, Cherokee 13057, Forsyth 13117, Henry 13151, Douglas 13097, Paulding 13223
  - [x] Phoenix — ~2 counties: Maricopa 04013, Pinal 04021
  - [x] Raleigh — ~5 counties: Wake 37183, Durham 37063, Johnston 37101, Orange 37135, Chatham 37037
- [x] Add `DEMAND_WINDOW_START_DAYS = 14` and `DEMAND_WINDOW_END_DAYS = 28` to `config.py`
- [x] Add NWS constants: `NWS_BASE_URL`, `NWS_USER_AGENT`, `NWS_RELEVANT_EVENTS`
- [x] Create `markets.py` with `classify_markets(results: list[DayResult], markets: list[Market]) -> dict[int, list[MarketResult]]`
  - For each day, for each market, check if ANY of its FIPS codes appear in flagged county_risks
  - Aggregate: highest risk level, count of affected counties, max probabilities
  - Return `dict[int, list[MarketResult]]` keyed by day
- [x] Add `if __name__ == "__main__":` block to `markets.py` for standalone testing
- [x] Test: run `python3 -m markets` against live data, verify market classification

#### Phase 2: Demand Window Calculator (`demand.py`)

- [x] Create `demand.py` with `DemandWindow` dataclass
- [x] `compute_windows(market_results: dict[int, list[MarketResult]], scan_date: date) -> list[DemandWindow]`
  - For each market with risk, resolve SPC day number to calendar date: `scan_date + timedelta(days=day - 1)`
  - For multi-day storms in same market, use the FIRST day as `storm_date`
  - Calculate `window_start = storm_date + DEMAND_WINDOW_START_DAYS`
  - Calculate `window_end = storm_date + DEMAND_WINDOW_END_DAYS`
  - Set `confirmed = True` if day == 1 and NWS has confirmed warnings (pass NWS data in later)
- [x] `format_window(window: DemandWindow) -> str` — returns human-readable string like "Volume bump Mar 16-30"
- [x] Add `if __name__ == "__main__":` block
- [x] Test: verify window dates for various day numbers

#### Phase 3: NWS Active Alerts (`sources/nws_alerts.py`)

- [x] Create `sources/nws_alerts.py` with `NWSAlert` dataclass
- [x] `fetch_alerts_for_states(states: list[str]) -> dict[str, list[NWSAlert]]`
  - Fetch `GET {NWS_BASE_URL}/alerts/active?area={STATE}&status=actual&message_type=alert,update`
  - Set `User-Agent` header (REQUIRED — 403 without it)
  - Set `Accept: application/geo+json` header
  - Filter client-side for `NWS_RELEVANT_EVENTS`
  - Retry once on 5xx/timeout, then skip state with warning
  - Pace requests: 0.5s delay between state fetches
- [x] `summarize_alerts(alerts: dict[str, list[NWSAlert]]) -> dict[str, dict]`
  - Group by event type: `{"TX": {"Tornado Warning": 2, "Severe Thunderstorm Watch": 1}}`
- [x] `has_confirmed_warnings(alerts: list[NWSAlert]) -> bool`
  - Returns True if any alerts have `certainty == "Observed"` or event contains "Warning" (not Watch)
  - Used as proxy for "It Happened" trigger
- [x] Add `if __name__ == "__main__":` block — fetch alerts for TX,OK and print summary
- [x] Test: run standalone, verify alert parsing and filtering

#### Phase 4: Slack Output with Alert Triggers (`output/slack.py`)

This is the most complex phase. Three message types, state management, and conversational formatting.

**Alert Trigger Logic:**

| Trigger | Condition | Day Range | Risk Threshold |
|---------|-----------|-----------|----------------|
| Heads Up | Market first shows risk | Days 4-8 | SLIGHT (3) or higher |
| Plan For It | Market at elevated risk | Days 1-3 | ENH (4) or higher |
| It Happened | NWS confirmed warning in market | Day 1 | Any + confirmed NWS warning |

Note: Day 4 included in "Heads Up" range (not a dead zone).

**State Management:**

- [x] `_load_state() -> dict` — load `data/alert_state.json`, return empty dict if missing/corrupt
- [x] `_save_state(state: dict) -> None` — write state atomically (write to temp, rename)
- [x] `_should_send(market_name: str, alert_type: str, storm_id: str, state: dict) -> bool`
  - `storm_id` = `f"{scan_date}-day{day}"` — identifies a unique storm forecast
  - Return False if same market + storm_id + alert_type was already sent
  - Return True if alert_type escalated (heads_up → plan_for_it → it_happened)
- [x] `_record_sent(market_name: str, alert_type: str, storm_id: str, state: dict) -> None`

**Message Formatting:**

- [x] `_format_heads_up(market_result, demand_window, alerts) -> dict`
  ```
  ⚠️ DFW Metro — Severe Weather Possible Next Wednesday
  15% any-severe probability across 8 of 12 counties.
  If confirmed, expect volume bump Mar 16-30.
  Action: heads up for Bryan, monitor forecast updates.
  ```
- [x] `_format_plan_for_it(market_result, demand_window, alerts) -> dict`
  ```
  🔴 OKC Metro — Hail Expected Wednesday
  ENHANCED risk. 27 counties affected. Hail: 30% | Tornado: 10% | Wind: 25%
  ⚠️ 2 Tornado Watches, 1 Severe Thunderstorm Watch active.
  Expect volume bump Mar 16-30.
  Action: flag for Bryan, check sub availability in OK.
  ```
- [x] `_format_it_happened(market_result, demand_window, alerts) -> dict`
  ```
  🚨 DFW Metro — Storm Confirmed Today
  MODERATE risk confirmed. 3 Tornado Warnings, 5 Severe Thunderstorm Warnings active.
  Volume window: Mar 16-30. Start scheduling crews.
  Action: Bryan to activate Dallas response plan.
  ```
- [x] `_format_summary(results, market_results, demand_windows) -> dict` — daily digest format matching spec.md Slack Output Format section
- [x] `_post_message(webhook_url: str, payload: dict) -> bool`
  - POST to webhook with `Content-Type: application/json`
  - Handle 429 with `Retry-After`
  - Log errors to stderr, return success/failure bool
  - Always include `text` field as fallback alongside `blocks`

**Public Interface:**

- [x] `post_alerts(results, market_results, demand_windows, nws_alerts, webhook_url) -> int`
  - Evaluate trigger conditions for each market
  - Check state file for deduplication
  - Send qualifying messages
  - Update state file
  - Return count of messages sent
- [x] `post_summary(results, market_results, demand_windows, nws_alerts, webhook_url) -> bool`
  - Send the full daily digest (non-trigger, just formatted output)
  - Used with `--slack` flag on `scan` command

#### Phase 5: CSV Export (`output/csv_export.py`)

- [x] `export_csv(path: str, results: list[DayResult], market_results: dict[int, list[MarketResult]], demand_windows: list[DemandWindow]) -> None`
- [x] Columns: `date, day_number, state, county, fips, risk_level, risk_name, hail_prob, tornado_prob, wind_prob, significant, market, demand_window_start, demand_window_end`
  - `market` column populated when county belongs to a defined market, else empty
  - `demand_window_start/end` populated when county's market has a demand window, else empty
- [x] Use stdlib `csv` module (no pandas dependency)
- [x] Write header row + one row per flagged county per day
- [x] Add `if __name__ == "__main__":` block
- [x] Test: run pipeline, export, verify CSV opens in Excel/Numbers

#### Phase 6: CLI Orchestration (`main.py`)

- [x] Refactor `run_scan()` to return `(results, data_available)` (prerequisite from Phase 0)
- [x] Add `run_markets(results, counties) -> (market_results, demand_windows)` helper
- [x] Add `run_alerts(market_results) -> nws_alerts` helper
- [x] Wire subcommands:
  - `scan` — existing pipeline + optional `--slack`, `--csv`
  - `markets` — scan + market classification + optional Visual Crossing (if API key set)
  - `alerts` — NWS alerts for states with configured markets
  - `full` — scan + markets + alerts, all outputs
- [x] Wire flags:
  - `--slack` — post to Slack (summary format for `scan`, alert triggers for `full`)
  - `--csv PATH` — export to CSV file
  - `--states TX,OK` — filter counties to these states (existing)
- [x] Handle missing optional config gracefully:
  - No `SLACK_WEBHOOK_URL` → skip Slack with warning
  - No `VISUAL_CROSSING_API_KEY` → skip Visual Crossing with note
- [x] Update console output to include market section and demand windows
- [x] Add `--quiet` flag to suppress stderr progress (useful for cron)

## Alternative Approaches Considered

**Slack message style: Block Kit vs mrkdwn**
- Block Kit offers structured sections, headers, dividers — better for the daily summary format
- Simple mrkdwn is more conversational — better for trigger alerts
- **Decision:** Use mrkdwn for trigger alerts (conversational), Block Kit for daily summary (structured)

**State management: SQLite vs JSON file**
- SQLite is more robust but adds a dependency and feels heavy for tracking ~10 markets
- JSON file is simpler, fits the "no database" philosophy, easy to inspect/reset
- **Decision:** JSON file in `data/alert_state.json`. Atomic writes (write temp → rename) prevent corruption.

**"It Happened" trigger: SPC storm reports vs NWS confirmed warnings**
- SPC reports require CSV parsing, reverse geocoding, damage threshold logic — significant standalone feature
- NWS confirmed warnings (certainty=Observed or Warning event type) are available through the same API we're already using
- **Decision:** NWS confirmed warnings as proxy for MVP. Can add SPC storm reports in Phase 2.

**Market FIPS: Census MSA vs manual curation**
- Census MSA boundaries are standardized but may not match Remi's actual service territories
- Manual curation is more work upfront but exactly matches business needs
- **Decision:** Manually curate in config.py. The roofing business knows which counties matter.

## System-Wide Impact

### Interaction Graph

```
main.py scan --slack
  → fetch_spc_outlooks()     [HTTP: SPC servers, 15 URLs]
  → load_counties()          [File I/O: data/us_counties.geojson]
  → match_counties()         [CPU: STRtree spatial indexing]
  → classify()               [CPU: threshold filtering]
  → classify_markets()       [CPU: FIPS set intersection]
  → compute_windows()        [CPU: date arithmetic]
  → fetch_alerts_for_states() [HTTP: api.weather.gov, 1 req per state]
  → _load_state()            [File I/O: data/alert_state.json]
  → post_alerts()            [HTTP: Slack webhook, 1 req per message]
  → _save_state()            [File I/O: data/alert_state.json]
```

### Error & Failure Propagation

| Error Source | Exception Types | Handling | Impact if Unhandled |
|-------------|----------------|----------|-------------------|
| SPC fetch fails | `RequestException`, `Timeout` | Retry once, skip URL | Some days missing risk data |
| County load fails | `OSError`, `JSONDecodeError` | Re-download, or exit | Pipeline cannot run |
| NWS fetch fails | `RequestException`, `Timeout` | Skip state with warning | No alert enrichment for that state |
| Slack post fails | `RequestException`, 429 | Retry with backoff, log | Alert not delivered |
| State file corrupt | `JSONDecodeError`, `OSError` | Reset to empty state | May re-send alerts |
| State file locked | `OSError` | Skip state update, log | May re-send on next run |

All failures are isolated — NWS failing doesn't block Slack; Slack failing doesn't block CSV. The pipeline degrades gracefully.

### State Lifecycle Risks

- **State file corruption:** Atomic writes (write temp → rename) prevent partial writes. On read failure, reset to empty state (may cause one round of duplicate alerts, acceptable).
- **State file growth:** Prune entries older than 14 days on each save. Max ~10 markets × 8 days = 80 entries.
- **Clock skew:** Capture `scan_date = date.today()` once at start of run, pass through entire pipeline. Don't call `date.today()` in multiple places.

### API Surface Parity

| Interface | Updated? | Notes |
|-----------|----------|-------|
| Console output | Yes | Add market section, demand window dates |
| Slack output | New | Three trigger types + daily summary |
| CSV export | New | County rows + market column + demand window columns |
| CLI | Yes | New commands (markets, alerts, full) and flags (--slack, --csv) |

## Acceptance Criteria

### Functional Requirements

- [ ] 10 Remi markets defined with county FIPS codes in `config.py`
- [ ] `python3 -m markets` classifies markets against live SPC data
- [ ] Demand windows computed as storm_date+14d through storm_date+28d
- [ ] Demand window dates appear in console, Slack, and CSV output
- [ ] NWS alerts fetched for states with at-risk markets
- [ ] NWS User-Agent header set correctly (no 403 errors)
- [ ] NWS alerts included in Slack messages for affected markets
- [ ] Slack "Heads Up" fires when market first shows SLIGHT+ risk, Days 4-8
- [ ] Slack "Plan For It" fires when market hits ENH+ risk, Days 1-3
- [ ] Slack "It Happened" fires when NWS confirmed warnings exist for Day 1 market
- [ ] Slack messages are conversational, include market owner name and action items
- [ ] Slack deduplication: same alert not re-sent on repeated cron runs
- [ ] Slack escalation: "Heads Up" → "Plan For It" → "It Happened" fires on upgrade
- [ ] CSV export includes: date, day_number, state, county, fips, risk_level, hail_prob, tornado_prob, wind_prob, market, demand_window_start, demand_window_end
- [ ] CLI `scan --slack` posts daily summary to Slack
- [ ] CLI `full --slack` runs full pipeline with trigger-based alerts
- [ ] CLI `alerts` fetches and displays NWS alerts
- [ ] CLI `markets` shows market-level risk classification
- [ ] CLI `--csv output.csv` exports to file
- [ ] Missing `SLACK_WEBHOOK_URL` skips Slack gracefully
- [ ] Missing `VISUAL_CROSSING_API_KEY` skips Visual Crossing gracefully

### Non-Functional Requirements

- [ ] NWS fetch paced at 0.5s between state requests
- [ ] Slack messages stay under 50 blocks / 40,000 chars
- [ ] State file writes are atomic (temp + rename)
- [ ] All HTTP calls have 30-second timeouts
- [ ] No bare `except Exception` in new code
- [ ] Each new module has `if __name__ == "__main__":` block

### Quality Gates

- [ ] All modules runnable standalone
- [ ] `python3 main.py full --states TX,OK` completes without errors
- [ ] Slack messages reviewed by team for tone/content before deploy

## Success Metrics

- Team actually uses the Slack alerts (not muted within a week)
- Demand window dates help scheduling — team references them in planning
- < 3 duplicate alerts per storm event (deduplication working)
- NWS enrichment adds real context (not just noise)

## Dependencies & Prerequisites

| Dependency | Status | Risk |
|-----------|--------|------|
| Core pipeline (Steps 1-5) | Done | None |
| Todo #009: run_scan returns results | Pending (P3) | Must fix first — blocks CLI wiring |
| Todo #005: probabilistic merge asymmetry | Pending (P2) | Should fix — affects Day 4-8 market classification |
| Slack webhook URL | Needs config | User must create webhook and add to .env |
| NWS API | Public, no key needed | Requires User-Agent header |
| Market FIPS data | Needs curation | Manually verified county lists |

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Market FIPS codes wrong | Medium | High | Verify against county GeoJSON names, cross-reference census data |
| NWS API changes format | Low | Medium | Pin to GeoJSON response format, add response validation |
| Slack webhook revoked | Low | High | Validate on startup, clear error message |
| State file corruption | Low | Low | Atomic writes, graceful reset on corrupt read |
| Alert fatigue (too many messages) | Medium | High | Deduplication, escalation-only sends, configurable thresholds |
| Day 4 gap (neither Heads Up nor Plan) | N/A | N/A | Resolved: Day 4 included in Heads Up range (4-8 days) |

## Future Considerations

- **Visual Crossing integration** (Step 8 from CLAUDE.md) — add `severerisk` scores per market when API key is present
- **SPC storm report parsing** — more accurate "It Happened" trigger using actual damage reports
- **"Stand down" messages** — notify when a previously flagged market clears
- **Historical tracking** — append each scan to a log file for trend analysis
- **Per-market Slack channels** — route DFW alerts to #dfw-ops, OKC to #okc-ops
- **Configurable thresholds per market** — some markets may want lower/higher sensitivity

## Sources & References

### Internal References
- Implementation plan: `docs/plans/2026-02-27-feat-core-weather-pipeline-steps-1-5-plan.md`
- P1 fixes: `docs/solutions/logic-errors/p1-code-review-fixes-feb2026.md`
- Prerequisites: `todos/009-pending-p3-run-scan-return-results.md`, `todos/005-pending-p2-probabilistic-merge-asymmetry.md`
- Existing patterns: `sources/spc.py` (HTTP fetching), `output/console.py` (rendering), `classifier.py` (data flow)
- Config: `config.py:100-106` (existing REMI_MARKETS)
- Spec: `spec.md` — Slack Output Format (lines 211-245), NWS API (lines 65-78), CLI Interface (lines 186-209)

### External References
- NWS API: https://www.weather.gov/documentation/services-web-api
- NWS Alerts: https://www.weather.gov/documentation/services-web-alerts
- Slack Incoming Webhooks: https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/
- Slack Block Kit: https://docs.slack.dev/reference/block-kit/blocks/
- Slack Rate Limits: https://docs.slack.dev/apis/web-api/rate-limits/
