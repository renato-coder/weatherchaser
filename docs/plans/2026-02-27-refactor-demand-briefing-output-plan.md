---
title: "refactor: Replace alert tiers with AI-generated demand briefing"
type: refactor
status: active
date: 2026-02-27
---

# Replace Alert Tiers with AI-Generated Demand Briefing

## Overview

Rebuild the output layer from weather dashboard to demand briefing. Kill the three-tier alert system (Heads Up / Plan For It / It Happened), kill the alert state file, and replace everything with a single twice-weekly Slack message written by Claude Sonnet. The message reads like a teammate summarizing what the team needs to know — no risk scores, no probability percentages, no FIPS codes, no county counts.

The briefing is the primary interface. The existing `scan`/`full` commands remain as power-user fallbacks for raw data.

## Problem Statement

The current Slack output is a weather dashboard, not a demand tool. It posts risk levels, probability percentages, FIPS county counts, and NWS alert summaries. The team doesn't think in those terms. They think: "Which markets are getting hit this week? When should we expect the phone to ring?"

The alert tier system adds complexity without value — three message types, a state file for dedup, escalation logic. The team just wants one message that tells them what they need to know.

## Proposed Solution

One new CLI command: `python main.py briefing --slack`

Pipeline: run the existing scan → classify at SLIGHT (3) threshold → filter to Days 1-5 only → classify markets → compute demand windows → serialize to structured JSON → send to Claude Sonnet → post AI-generated text to Slack.

### Target Output

```
📋 Storm Brief — Mon Mar 2

DFW and OKC look active this week. Hail likely Wed-Thu across north
Texas and central Oklahoma. If it hits, expect volume to pick up
around March 18.

Houston, Nashville, Denver, Atlanta, Phoenix, Raleigh, San Antonio,
Minneapolis — all quiet. Nothing on the radar.

That's it for this week.
```

## Technical Approach

### Architecture

```
main.py briefing --slack
  → run_scan()                           # existing: SPC + counties + match
  → classify(matched, categorical_min=3) # CHANGED: lower threshold for briefing
  → filter to Days 1-5 only             # NEW: drop Days 6-8
  → classify_markets(results)            # existing: FIPS join
  → compute_windows(market_results)      # existing: demand dates
  → prepare_briefing_data()              # NEW: serialize for Claude
  → generate_briefing()                  # NEW: Claude Sonnet API call
  → post to Slack webhook               # existing: _post_message()
  → print to stdout                     # always, for preview/logging
```

New/modified files:

| File | Change | Role |
|------|--------|------|
| `output/briefing.py` | **NEW** | Claude API integration, data prep, briefing generation |
| `classifier.py` | **MODIFY** | Add optional `categorical_min` parameter |
| `main.py` | **MODIFY** | Add `briefing` command |
| `output/slack.py` | **MODIFY** | Remove alert tier code, keep `_post_message` and `post_summary` |
| `config.py` | **MODIFY** | Add `BRIEFING_CATEGORICAL_MIN`, `BRIEFING_MAX_DAY` constants |
| `requirements.txt` | **MODIFY** | Add `anthropic` SDK |

### Key Design Decisions

**1. SLIGHT threshold — briefing-only, not global**

The existing classifier uses `CAT_THRESHOLDS["spc_categorical_min"] = 4` (ENH). The briefing wants SLIGHT (3)+. Rather than changing the global threshold (which would affect console, CSV, and existing Slack summary), add an optional `categorical_min` parameter to `classify()`:

```python
# classifier.py
def classify(
    matched: dict[int, list[CountyRisk]],
    data_available: bool = True,
    categorical_min: int | None = None,  # NEW: override threshold
) -> list[DayResult]:
```

The briefing pipeline calls `classify(matched, categorical_min=3)`. All other commands use the default (ENH/4). No existing behavior changes.

**2. Days 4-5 probabilistic data — treat 15%+ as meeting threshold**

Days 4-5 have no categorical levels (always 0). The existing `_meets_threshold()` already catches counties with probabilistic risk (hail ≥ 15%, tornado ≥ 5%, wind ≥ 15%). So SLIGHT-equivalent Day 4-5 counties already pass through when `categorical_min` is lowered. The briefing filter just needs to exclude Days 6-8:

```python
# In briefing pipeline
briefing_results = [dr for dr in results if dr.day <= BRIEFING_MAX_DAY]
```

**3. Claude API prompt — structured data in, conversational text out**

Pass a clean JSON payload to Claude Sonnet with explicit format instructions. The system prompt defines the persona (Remi team member), constraints (no jargon, no percentages, plain English), and structure (active markets grouped, quiet markets in one line, casual sign-off).

The user message contains the serialized market data:

```python
# output/briefing.py

def prepare_briefing_data(
    market_results: dict[int, list[MarketResult]],
    demand_windows: list[DemandWindow],
    scan_date: date,
) -> dict:
    """Serialize market results into a clean dict for Claude."""
```

Fields passed to Claude per active market:
- `market_name` ("Dallas-Fort Worth")
- `short_name` ("DFW")
- `states` (["TX"])
- `owner` ("Bryan") — so Claude can say "flag for Bryan"
- `days_at_risk` — list of `{day, day_date, risk_name, hazard_types}`
- `demand_window` — `{start_date, end_date}` formatted as "Mar 18 – Apr 1"

Quiet markets: just a list of names.

**4. No state file, no dedup**

Each briefing is a standalone snapshot. The `alert_state.json` system is removed entirely. No `_load_state`, `_save_state`, `_should_send`, `_record_sent`. If the cron fires twice by accident, the team gets two messages — acceptable for a twice-weekly cadence.

**5. Console preview is the default**

`python main.py briefing` (no `--slack`) prints the AI-generated briefing to stdout. This is the test/preview mode. Adding `--slack` posts it to the webhook AND prints to stdout.

**6. NWS alerts excluded from briefing**

The twice-weekly cadence makes real-time alert data stale. NWS alerts stay in the `full` command for power users. The briefing pipeline skips `fetch_alerts_for_states()` entirely — faster execution, fewer failure points.

**7. Keep scan/full as-is, remove alert tiers from full --slack**

- `scan --slack` → continues using `post_summary()` (daily digest Block Kit format)
- `full --slack` → switches from `post_alerts()` to `post_summary()` (killing the tier system)
- `briefing --slack` → new AI-generated briefing
- Remove from `output/slack.py`: `post_alerts()`, `_format_heads_up()`, `_format_plan_for_it()`, `_format_it_happened()`, `_load_state()`, `_save_state()`, `_should_send()`, `_record_sent()`, `_ALERT_LEVELS`

### Implementation Phases

#### Phase 1: Foundation — Config and Classifier Changes

- [x] Add constants to `config.py`:
  ```python
  BRIEFING_CATEGORICAL_MIN = 3   # SLIGHT
  BRIEFING_MAX_DAY = 5           # Only Days 1-5
  ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
  ANTHROPIC_MAX_TOKENS = 1024
  ```
- [x] Add `ANTHROPIC_API_KEY` to `.env` loading (already handled by `python-dotenv`)
- [x] Modify `classifier.py` — add optional `categorical_min` parameter to `classify()`:
  - Default: `None` (uses `CAT_THRESHOLDS["spc_categorical_min"]` = 4)
  - When set: overrides the categorical check in `_meets_threshold()`
  - Probabilistic thresholds remain unchanged
- [x] Add `anthropic` to `requirements.txt`
- [x] Test: `python3 -m classifier` still works with default threshold
- [x] Test: calling `classify(matched, categorical_min=3)` returns more counties than `classify(matched)`

#### Phase 2: Briefing Module (`output/briefing.py`)

- [x] Create `output/briefing.py` with:

  **`prepare_briefing_data()`** — serialize market results for Claude:
  ```python
  def prepare_briefing_data(
      market_results: dict[int, list[MarketResult]],
      demand_windows: list[DemandWindow],
      scan_date: date,
  ) -> dict:
  ```
  Returns:
  ```json
  {
    "scan_date": "2026-03-02",
    "briefing_day": "Monday",
    "active_markets": [
      {
        "name": "Dallas-Fort Worth",
        "short_name": "DFW",
        "states": ["TX"],
        "owner": "Bryan",
        "risk_days": [
          {"day": 3, "date": "Wed Mar 4", "risk": "SLIGHT", "hazards": ["hail", "wind"]}
        ],
        "demand_window": "Mar 18 – Apr 1"
      }
    ],
    "quiet_markets": ["Houston", "Nashville", "Denver", "Atlanta", "Phoenix", "Raleigh", "San Antonio", "Minneapolis"]
  }
  ```

  **`generate_briefing()`** — call Claude Sonnet:
  ```python
  def generate_briefing(briefing_data: dict) -> str | None:
  ```
  - Create `anthropic.Anthropic(timeout=30.0, max_retries=2)` client
  - System prompt (see below)
  - User message: JSON payload
  - Return `message.content[0].text` or `None` on failure
  - Log token usage to stderr
  - Handle errors: `APIConnectionError`, `RateLimitError`, `AuthenticationError`, `APIStatusError`

  **System Prompt:**
  ```
  You write twice-weekly storm briefings for Remi, a roofing company.
  You'll receive structured data about which metro markets have severe
  weather risk this week and when demand might spike afterward.

  Write a short Slack message (3-5 short paragraphs) that tells the
  team what they need to know. Rules:

  - Start with: 📋 Storm Brief — {day_of_week} {month} {day}
  - Group active markets together. For each, write 1-2 plain English
    sentences: what's coming (hail, tornado risk, storms), when, and
    when demand would spike. Use the market owner's name if provided.
  - Group quiet markets in one line: "{names} — all quiet."
  - End with a casual sign-off like "That's it for this week."
  - No weather jargon. No risk scores. No probability percentages.
    No FIPS codes. No county counts. Write like a teammate, not a
    dashboard.
  - Keep it under 200 words.
  ```

  **`post_briefing()`** — public interface:
  ```python
  def post_briefing(
      briefing_text: str,
      webhook_url: str,
  ) -> bool:
  ```
  - Uses existing `output.slack._post_message()` internally
  - Payload: `{"text": briefing_text}` (plain mrkdwn, no Block Kit)

- [x] Add `if __name__ == "__main__":` block for standalone testing
- [x] Test: run against live data, verify Claude generates a clean briefing

#### Phase 3: CLI Wiring (`main.py`)

- [x] Add `briefing` subcommand to argparse:
  ```python
  briefing_parser = subparsers.add_parser(
      "briefing", help="AI-generated demand briefing for Slack",
  )
  briefing_parser.add_argument("--slack", action="store_true")
  briefing_parser.add_argument("--states", help="Filter states")
  ```
- [x] Implement `_cmd_briefing(args)`:
  1. `run_scan(states=args.states)` — full SPC pipeline
  2. `classify(matched, categorical_min=BRIEFING_CATEGORICAL_MIN)` — lower threshold
  3. Filter results to `day <= BRIEFING_MAX_DAY`
  4. `classify_markets(results)` — FIPS join
  5. `compute_windows(market_results)` — demand dates
  6. `prepare_briefing_data(...)` — serialize
  7. `generate_briefing(data)` — Claude API call
  8. Print briefing to stdout (always)
  9. If `--slack` and webhook set: post to Slack
  10. Handle missing `ANTHROPIC_API_KEY`: warn and exit

- [x] Note: the briefing pipeline needs access to the raw `matched` data (pre-classification) so it can call `classify()` with the lower threshold. Refactor `run_scan()` to optionally return `matched` dict, OR extract a `run_briefing_pipeline()` helper that runs the full chain with the briefing threshold.

  Cleanest approach — extract a `_run_pipeline()` that returns intermediate results:
  ```python
  def _run_pipeline(states=None, categorical_min=None):
      """Run scan pipeline. Returns (results, market_results, windows, any_data)."""
      outlooks, any_data = fetch_spc_outlooks()
      counties = load_counties()
      if states: counties = [c for c in counties if ...]
      matched = match_counties(outlooks, counties)
      results = classify(matched, data_available=any_data, categorical_min=categorical_min)
      market_results = classify_markets(results)
      windows = compute_windows(market_results)
      return results, market_results, windows, any_data
  ```

- [x] Test: `python3 main.py briefing` — prints briefing to stdout
- [ ] Test: `python3 main.py briefing --slack` — posts to Slack and prints to stdout

#### Phase 4: Clean Up Alert Tier Code

- [x] Remove from `output/slack.py`:
  - `_ALERT_LEVELS` dict
  - `_load_state()`, `_save_state()`, `_should_send()`, `_record_sent()`
  - `_format_heads_up()`, `_format_plan_for_it()`, `_format_it_happened()`
  - `post_alerts()`
  - `_STATE_PATH` constant
  - All imports only used by removed code (`tempfile`, etc.)
- [x] Keep in `output/slack.py`:
  - `_RISK_EMOJI` (used by `_format_summary`)
  - `_format_summary()` (used by `scan --slack`, `markets --slack`)
  - `post_summary()` (public interface for daily digest)
  - `_post_message()` (used by briefing module and post_summary)
- [x] Update `full --slack` in `main.py` to use `post_summary()` instead of `post_alerts()`
- [x] Delete `data/alert_state.json` if it exists
- [x] Remove the standalone test code in `output/slack.py` `__main__` block that references alert triggers, replace with summary-only test
- [x] Test: `python3 main.py full --slack` still works (now posts summary instead of alerts)
- [x] Test: `python3 main.py scan --slack` still works unchanged

#### Phase 5: Documentation and Cron Setup

- [ ] Update `CLAUDE.md`:
  - Add `ANTHROPIC_API_KEY` to Environment Variables section
  - Add `briefing` to the CLI commands list
  - Note: briefing is the primary interface, scan/full are power-user tools
- [ ] Add cron example to output:
  ```bash
  # Storm briefing: Monday and Thursday at 7:00 AM CT (13:00 UTC)
  0 13 * * 1,4 cd /path/to/weatherchaser && python3 main.py briefing --slack --quiet
  ```
- [ ] Update `requirements.txt` with `anthropic>=0.40.0`
- [ ] Test full end-to-end: `python3 main.py briefing --slack`

## Alternative Approaches Considered

**Template-based briefing vs Claude API**
- Templates produce consistent but robotic output ("DFW: SLIGHT risk on Day 3. Demand window: Mar 18-Apr 1.")
- Claude produces natural, varied prose that reads like a person wrote it
- **Decision:** Claude API. The user explicitly requested it. Cost is ~$0.01/call, ~$1/month at twice-weekly cadence.

**Lower global classifier threshold vs briefing-specific threshold**
- Lowering globally to SLIGHT (3) would show more data in console/CSV/existing Slack summary
- Briefing-specific threshold keeps existing behavior unchanged
- **Decision:** Briefing-specific. Add `categorical_min` parameter to `classify()`. No existing output changes.

**Include NWS alerts in briefing vs exclude**
- Including adds real-time context ("3 watches active") but the twice-weekly cadence makes it stale
- Excluding keeps the briefing focused on forward-looking demand
- **Decision:** Exclude. NWS alerts remain available via `python3 main.py alerts` or `full`.

**Remove `scan --slack` and `full --slack` vs keep them**
- Removing simplifies the codebase but breaks backward compatibility
- Keeping preserves power-user access to raw data
- **Decision:** Keep both. `full --slack` switches from `post_alerts()` to `post_summary()`.

## System-Wide Impact

### Interaction Graph

```
main.py briefing --slack
  → fetch_spc_outlooks()        [HTTP: SPC, 15 URLs]
  → load_counties()             [File I/O: data/us_counties.geojson]
  → match_counties()            [CPU: STRtree spatial index]
  → classify(categorical_min=3) [CPU: lower threshold]
  → classify_markets()          [CPU: FIPS set intersection]
  → compute_windows()           [CPU: date arithmetic]
  → prepare_briefing_data()     [CPU: serialization]
  → generate_briefing()         [HTTP: Anthropic API, 1 request]
  → _post_message()             [HTTP: Slack webhook, 1 request]
```

Two new HTTP dependencies: Anthropic API and the existing Slack webhook. SPC/NWS are existing.

### Error & Failure Propagation

| Error Source | Handling | Impact |
|-------------|----------|--------|
| SPC fetch fails | Existing: retry once, skip URL | Partial data — briefing notes uncertainty |
| Claude API auth failure | Log to stderr, exit non-zero | No briefing posted. Cron monitoring catches it |
| Claude API rate limit/5xx | SDK auto-retries 2x, then log + exit non-zero | Same as above |
| Claude API returns truncated text | Log warning, post anyway (still useful) | Slightly shorter briefing |
| Slack webhook fails | Existing: retry on 429, log error | Briefing printed to stdout but not posted |
| ANTHROPIC_API_KEY missing | Warn to stderr and exit | Clean failure, cron monitoring catches it |

### State Lifecycle Risks

**Removed:** `data/alert_state.json` — no more persistent state. Each briefing run is stateless. No orphaned state, no corruption risk, no cleanup needed.

### API Surface Parity

| Interface | Before | After |
|-----------|--------|-------|
| `briefing --slack` | N/A | NEW: AI briefing to Slack |
| `briefing` (no flag) | N/A | NEW: AI briefing to stdout |
| `scan --slack` | `post_summary()` | Unchanged |
| `full --slack` | `post_alerts()` | CHANGED → `post_summary()` |
| `scan` / `markets` / `alerts` / `full` | Console output | Unchanged |
| `--csv` | CSV export | Unchanged |

## Acceptance Criteria

### Functional Requirements

- [ ] `python3 main.py briefing` prints an AI-generated demand briefing to stdout
- [ ] `python3 main.py briefing --slack` posts the briefing to Slack AND prints to stdout
- [ ] Briefing only includes markets with SLIGHT (3)+ risk within Days 1-5
- [ ] Days 6-8 data is completely excluded from the briefing
- [ ] Active markets are grouped with 1-2 sentences each (what's coming, when, demand window)
- [ ] Quiet markets are grouped in one line ("all quiet")
- [ ] No weather jargon: no risk scores, probability percentages, FIPS codes, or county counts
- [ ] Briefing reads like a teammate writing a Slack message
- [ ] Market owner names referenced where set (e.g., "flag for Bryan")
- [ ] Demand window dates included for active markets
- [ ] When zero markets are at risk, briefing still posts ("all quiet across all markets")
- [ ] Existing `scan`, `markets`, `alerts`, `full` commands continue to work unchanged
- [ ] `full --slack` posts summary format (not alert triggers)
- [ ] Alert state file (`data/alert_state.json`) system is removed
- [ ] Missing `ANTHROPIC_API_KEY` produces a clear error message and exits cleanly
- [ ] Claude API failure logs error to stderr and exits non-zero (cron monitoring)

### Non-Functional Requirements

- [ ] Claude API call completes in < 30 seconds
- [ ] Total pipeline runtime < 60 seconds (SPC + classify + Claude + Slack)
- [ ] Claude API cost < $0.02 per briefing call
- [ ] Briefing text < 500 words (enforced via max_tokens and prompt instructions)

### Quality Gates

- [ ] Each module runnable standalone via `if __name__ == "__main__":`
- [ ] `python3 main.py briefing` works against live SPC data
- [ ] `python3 main.py scan` / `full` / `markets` / `alerts` all still work

## Dependencies & Prerequisites

| Dependency | Type | Notes |
|-----------|------|-------|
| `anthropic` Python SDK | New pip package | `pip install anthropic` |
| `ANTHROPIC_API_KEY` | New env var | User confirms they have a key |
| Claude Sonnet (`claude-sonnet-4-20250514`) | External API | ~$0.009/call |
| Existing pipeline (Steps 1-5) | Internal | No changes needed |
| `output/slack._post_message()` | Internal | Reused for posting |

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Claude API produces inconsistent/bad output | Medium | Low | Strong system prompt with explicit constraints; briefing always printed to stdout for review |
| Claude API is down during cron run | Low | Medium | Exit non-zero for cron monitoring; team can run manually later |
| Lowering classifier threshold breaks existing outputs | Low | High | Threshold is parameter-based, default unchanged. Only briefing uses lower value |
| Team misses real-time storm alerts (tier system removed) | Medium | Medium | Acknowledged tradeoff. `full` command with console output remains for ad-hoc checks |
| Claude generates text exceeding Slack limits | Very Low | Low | `max_tokens=1024` and "under 200 words" prompt instruction cap output |

## Future Considerations

- **Per-market owner routing**: Post market-specific alerts to owner-specific Slack channels or DMs
- **SPC storm reports integration**: Add confirmed storm damage data for "It Happened" context in future briefings
- **Briefing history**: Store past briefings for trend analysis (currently stateless)
- **Visual Crossing integration**: Add severerisk scores to briefing data for richer AI context
- **Custom briefing schedule**: Config-driven schedule instead of hardcoded Mon/Thu assumption

## Sources & References

### Internal References

- Existing pipeline: `sources/spc.py`, `geo/matcher.py`, `classifier.py`, `markets.py`, `demand.py`
- Current Slack output: `output/slack.py` (alert tiers to be removed)
- Market definitions: `config.py:110-144` (10 markets, 88 FIPS codes)
- Classifier threshold: `config.py:88-94` (`spc_categorical_min = 4`)

### External References

- Anthropic Python SDK: https://github.com/anthropics/anthropic-sdk-python
- Claude Sonnet pricing: ~$3/$15 per MTok (input/output)
- Slack Incoming Webhooks: https://api.slack.com/messaging/webhooks
