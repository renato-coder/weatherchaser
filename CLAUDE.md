# CLAUDE.md — Remi CAT Event Tracker

## Project Overview

Build a Python CLI tool that tracks severe weather (CAT events) across US states and counties using free government weather data. The tool helps a roofing company (Remi) plan ahead for post-storm demand.

Read `spec.md` for full requirements, data sources, and architecture.

## Implementation Order

Build in this exact sequence. Each step should be testable before moving to the next.

### Step 1: SPC Outlook Fetcher
File: `sources/spc.py`

- Fetch SPC convective outlook GeoJSON files for Days 1-3 (categorical + hail/tornado/wind) and Days 4-8 (probabilistic)
- Parse the GeoJSON into a list of risk polygons with their metadata (risk level, day, type)
- Handle network errors gracefully (retry once, then skip with warning)
- The GeoJSON files use a `LABEL` field for risk category and `fill`/`stroke` for colors
- For Days 1-2: fetch categorical, hail, tornado, and wind separately
- For Day 3: fetch categorical and probabilistic
- For Days 4-8: fetch probabilistic only
- Test: run fetcher and print number of polygons found per day/type

### Step 2: County Boundary Loader
File: `geo/counties.py`

- Download US county boundaries GeoJSON on first run, cache locally in `data/` directory
- Source: `https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json`
- Parse into county records with FIPS code, name, state, and centroid (lat/lon)
- Pre-compute centroids using Shapely for fast point-in-polygon later
- Test: print total county count (should be ~3,143) and a few sample records

### Step 3: Geographic Matcher
File: `geo/matcher.py`

- For each SPC risk polygon, find which county centroids fall inside it
- Use Shapely `Point.within(polygon)` for each county centroid against each risk polygon
- Return a mapping: county FIPS -> highest risk level it falls within
- Optimization: use a bounding box pre-filter to skip counties clearly outside the polygon
- Aggregate results to state level: state -> list of affected counties with risk levels
- Test: run against current SPC data and print states with counties at risk

### Step 4: Classifier
File: `classifier.py`

- Apply CAT thresholds from `config.py` to the matched results
- Classify each county/state as: CLEAR, MARGINAL, SLIGHT, ENHANCED, MODERATE, HIGH
- For each flagged area, include: risk level, hail/tornado/wind probabilities, valid timeframe
- Test: print classified results for current data

### Step 5: Console Output
File: `output/console.py`

- Pretty-print results to terminal using `rich` library
- Group by day, then by risk level (highest first)
- Show state summaries with county counts
- Color-code by risk level
- Test: run full pipeline and verify readable output

### Step 6: Slack Output
File: `output/slack.py`

- Format results as Slack webhook payload (Block Kit or simple markdown)
- Post to configured webhook URL
- Include emoji indicators for risk levels
- Match the format shown in spec.md under "Slack Output Format"
- Test: post a test message to Slack

### Step 7: NWS Active Alerts
File: `sources/nws_alerts.py`

- Fetch active alerts from `api.weather.gov` for states that have CAT risk
- Filter for relevant alert types only (tornado, severe thunderstorm, hurricane, extreme wind)
- Include alert count and summary in output
- Required: set User-Agent header per NWS API requirements
- Test: fetch alerts for a state and print summary

### Step 8: Visual Crossing Market Detail (Optional)
File: `sources/visual_crossing.py`

- For configured Remi markets, fetch severerisk scores
- Only runs if `VISUAL_CROSSING_API_KEY` env var is set
- Add market-level detail to output
- Test: fetch severerisk for one market and print result

### Step 9: CSV Export
File: `output/csv_export.py`

- Export current scan results to CSV
- Columns: date, day_number, state, county, fips, risk_level, hail_prob, tornado_prob, wind_prob
- Test: export and verify CSV opens correctly

### Step 10: CLI Orchestrator
File: `main.py`

- Wire everything together with `argparse` CLI
- Commands: `scan`, `markets`, `alerts`, `full`
- Flags: `--slack`, `--csv`, `--states`
- Handle missing optional dependencies gracefully (e.g., no VC API key = skip markets)

## Code Style

- Python 3.10+
- Type hints on all function signatures
- Docstrings on public functions (one-liner is fine)
- No classes unless genuinely needed. Prefer functions and dataclasses.
- Use `dataclasses` for structured data, not plain dicts
- Print progress to stderr, results to stdout
- All API calls should have timeouts (30 seconds)
- Use `requests` for HTTP, not `urllib`

## Environment Variables

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
VISUAL_CROSSING_API_KEY=your_key_here
ANTHROPIC_API_KEY=sk-ant-...  # Required for briefing command
```

Use `python-dotenv` to load from `.env` file.

## Testing Approach

No formal test framework needed for MVP. Each module should be runnable standalone:

```bash
python -m sources.spc          # Fetches and prints SPC data
python -m geo.counties          # Loads and prints county stats
python -m geo.matcher           # Runs matcher against live SPC data
python -m classifier            # Runs full classification
python main.py scan             # Full scan pipeline
python main.py briefing         # AI-generated demand briefing (primary interface)
python main.py briefing --slack # Post briefing to Slack
python main.py full             # Full pipeline: scan + markets + alerts
```

### Cron Setup (briefing)

```bash
# Storm briefing: Monday and Thursday at 7:00 AM CT (13:00 UTC)
0 13 * * 1,4 cd /path/to/weatherchaser && python3 main.py briefing --slack --quiet
```

Add `if __name__ == "__main__":` blocks to each module for standalone testing.

## Common Gotchas

1. **SPC GeoJSON URLs may 404** if no outlook has been issued yet for that day/time. Handle gracefully.
2. **SPC GeoJSON geometry may be MultiPolygon** — handle both Polygon and MultiPolygon.
3. **County boundaries GeoJSON is ~25MB** — download once, cache in `data/` directory, don't re-download.
4. **NWS API requires User-Agent header** or it returns 403.
5. **GeoJSON coordinates are [longitude, latitude]** not [lat, lon]. Shapely handles this correctly but be careful with any manual coordinate work.
6. **SPC Day 4-8 outlooks may not exist** if no significant risk is forecast. The URL will 404. This is normal.
7. **Visual Crossing free tier is 1,000 records/day** — with 10 markets x 7 days = 70 records per call, we're well within limits.

## Do NOT

- Do not build a web dashboard for MVP. Console + Slack is enough.
- Do not try to geocode addresses. We use county FIPS centroids.
- Do not install heavy GIS dependencies (PostGIS, GDAL) if avoidable. Shapely + geopandas should handle everything.
- Do not over-engineer the config. A Python file with constants is fine. No YAML/TOML config parsers.
- Do not build a database. This is a stateless scan tool.
