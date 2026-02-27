# Remi CAT Event Tracker — Spec

## What This Is

A Python CLI tool that monitors severe weather forecasts across the US and tells us which states and counties are expecting CAT-level weather events (hail, tornadoes, severe wind) in the next 1-8 days. Output goes to Slack and optionally a simple dashboard.

This is for demand planning. When a hailstorm hits a metro, roofing demand spikes 2-4 weeks later. If we see it coming, we can pre-position outreach, line up subcontractors, and beat competitors to the market.

## What "CAT Event" Means For Us

In roofing, a CAT (catastrophe) event = severe weather that damages roofs. The signals that matter:

- **Hail ≥ 1 inch diameter** (the threshold for roof damage claims)
- **Tornadoes** (any confirmed or warned)
- **Severe wind ≥ 58 mph** (the NWS severe thunderstorm threshold)
- **Hurricanes / tropical storms** (if applicable to our markets)

We do NOT care about: rain, snow, cold, heat, fog, flooding (unless combined with wind).

## Data Sources (Priority Order)

### 1. SPC Convective Outlooks (Primary — FREE, no API key)

The Storm Prediction Center issues convective outlooks for Days 1-8 covering the entire contiguous US. This is the gold standard for severe weather forecasting.

**Data format:** GeoJSON/Shapefiles with polygons for each risk area
**Update frequency:** Multiple times daily for Day 1-2, daily for Day 3-8

**Download URLs (stable, no auth required):**
- Day 1 categorical: `https://www.spc.noaa.gov/products/outlook/day1otlk_cat.lyr.geojson`
- Day 1 hail probability: `https://www.spc.noaa.gov/products/outlook/day1otlk_hail.lyr.geojson`
- Day 1 tornado probability: `https://www.spc.noaa.gov/products/outlook/day1otlk_torn.lyr.geojson`
- Day 1 wind probability: `https://www.spc.noaa.gov/products/outlook/day1otlk_wind.lyr.geojson`
- Day 2 categorical: `https://www.spc.noaa.gov/products/outlook/day2otlk_cat.lyr.geojson`
- Day 2 hail: `https://www.spc.noaa.gov/products/outlook/day2otlk_hail.lyr.geojson`
- Day 2 tornado: `https://www.spc.noaa.gov/products/outlook/day2otlk_torn.lyr.geojson`
- Day 2 wind: `https://www.spc.noaa.gov/products/outlook/day2otlk_wind.lyr.geojson`
- Day 3 categorical: `https://www.spc.noaa.gov/products/outlook/day3otlk_cat.lyr.geojson`
- Day 3 probabilistic: `https://www.spc.noaa.gov/products/outlook/day3otlk_prob.lyr.geojson`
- Days 4-8 probabilistic: `https://www.spc.noaa.gov/products/exper/day4-8/day{N}prob.lyr.geojson` (where N = 4-8)

**Shapefiles (alternative, zipped):**
- `https://www.spc.noaa.gov/products/outlook/day1otlk-shp.zip`
- `https://www.spc.noaa.gov/products/outlook/day2otlk-shp.zip`
- `https://www.spc.noaa.gov/products/outlook/day3otlk-shp.zip`
- `https://www.spc.noaa.gov/products/exper/day4-8/day4prob-shp.zip` (through day8)

**SPC Risk Categories (Day 1-3):**
| Level | Label | Code | What It Means For Us |
|-------|-------|------|---------------------|
| 1 | TSTM | General Thunder | Ignore — no CAT signal |
| 2 | MRGL (Marginal) | 1 | Watch — possible isolated severe |
| 3 | SLGT (Slight) | 2 | Alert — scattered severe likely |
| 4 | ENH (Enhanced) | 3 | **High Alert — significant severe expected** |
| 5 | MDT (Moderate) | 4 | **CAT Event — widespread severe, major damage likely** |
| 6 | HIGH | 5 | **Major CAT — rare, outbreak-level event** |

**Our CAT thresholds:**
- ENH or higher (level ≥ 3) on categorical = flag as CAT risk
- Hail probability ≥ 15% = flag as hail risk
- Tornado probability ≥ 5% = flag as tornado risk
- Wind probability ≥ 15% = flag as wind risk
- Any "significant severe" hatched area = flag as major CAT

### 2. NWS Alerts API (Secondary — FREE, no API key)

Real-time watches, warnings, and advisories. Use this for "right now" situational awareness.

**Base URL:** `https://api.weather.gov/alerts/active`
**Relevant alert types to filter for:**
- Tornado Warning / Watch
- Severe Thunderstorm Warning / Watch
- Hurricane Warning / Watch
- Extreme Wind Warning

**Query by state:** `https://api.weather.gov/alerts/active?area={STATE_CODE}`
**Query by zone:** `https://api.weather.gov/alerts/active?zone={ZONE_ID}`

**Required header:** `User-Agent: (remi-cat-tracker, contact@remirc.com)`

### 3. Visual Crossing (Tertiary — FREE tier, API key required)

Use for location-specific `severerisk` scores (0-100) on our specific markets. Supplements the SPC data with a single numeric risk score.

**API:** Timeline Weather API
**Free tier:** 1,000 records/day
**Key field:** `severerisk` — 0-100 score combining CAPE, CIN, predicted rain/wind
  - < 30 = low risk
  - 30-70 = moderate risk
  - > 70 = high risk (CAT-level)

**Also returns:** Active government alerts in the same API call

**Endpoint pattern:**
```
https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{location}/next7days?unitGroup=us&key={API_KEY}&include=days,alerts&elements=datetime,severerisk,windgust,precipprob,conditions
```

## Architecture

### Core Components

```
cat_tracker/
├── CLAUDE.md              # Claude Code instructions (see below)
├── spec.md                # This file
├── requirements.txt       # Python dependencies
├── config.py              # Markets, thresholds, API keys, Slack webhook
├── main.py                # CLI entrypoint and orchestrator
├── sources/
│   ├── spc.py             # Fetch + parse SPC convective outlooks
│   ├── nws_alerts.py      # Fetch active NWS alerts
│   └── visual_crossing.py # Fetch severerisk scores per market
├── geo/
│   ├── counties.py        # County boundary lookup (FIPS -> polygon)
│   └── matcher.py         # Point-in-polygon: which counties fall in SPC risk areas
├── classifier.py          # Apply CAT thresholds, classify markets
├── output/
│   ├── slack.py            # Format and post to Slack webhook
│   ├── console.py          # Pretty-print to terminal
│   └── csv_export.py       # Export current status to CSV
└── data/
    └── us_counties.geojson # US county boundaries (download once, ~25MB)
```

### How It Works (Step by Step)

1. **Fetch SPC outlooks** for Days 1-3 (categorical + hail/tornado/wind probabilities) and Days 4-8 (probabilistic)
2. **Load US county boundaries** from local GeoJSON file
3. **For each SPC risk polygon**, find which counties intersect it using Shapely
4. **Classify each county** based on the highest risk level it falls within
5. **Aggregate to state level** — "Texas has 47 counties in ENH or higher for Day 1"
6. **Optionally fetch Visual Crossing** severerisk for specific Remi markets
7. **Optionally fetch NWS active alerts** for states with CAT risk
8. **Output** results to console, Slack, and/or CSV

### State/County Mapping

This is the key technical piece. SPC publishes risk areas as polygons. We need to know which states and counties those polygons cover.

**Approach:** Download US county boundaries GeoJSON once (Census Bureau TIGER data). On each run, use Shapely to check which county centroids or polygons intersect the SPC risk polygons.

**County boundaries source:**
```
https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json
```

Or from Census Bureau:
```
https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_county_500k.zip
```

**Optimization:** Use county centroids for fast point-in-polygon checks rather than full polygon intersection. A county centroid inside a risk area is close enough for our purposes.

### Configuration

```python
# config.py

# Markets we specifically track (for Visual Crossing detail)
REMI_MARKETS = [
    {"name": "Dallas-Fort Worth", "zip": "75201", "state": "TX"},
    {"name": "Houston", "zip": "77001", "state": "TX"},
    {"name": "Denver", "zip": "80201", "state": "CO"},
    {"name": "Oklahoma City", "zip": "73101", "state": "OK"},
    {"name": "Nashville", "zip": "37201", "state": "TN"},
    # Add more as needed
]

# CAT classification thresholds
CAT_THRESHOLDS = {
    "spc_categorical_min": 3,      # ENH or higher
    "hail_prob_min": 15,            # 15%+ hail probability
    "tornado_prob_min": 5,          # 5%+ tornado probability
    "wind_prob_min": 15,            # 15%+ wind probability
    "severerisk_min": 50,           # Visual Crossing score
}

# Slack webhook URL (set via env var)
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# Visual Crossing API key (set via env var)
VC_API_KEY = os.environ.get("VISUAL_CROSSING_API_KEY", "")
```

## CLI Interface

```bash
# Full scan — all US states, Days 1-8, output to console
python main.py scan

# Full scan, post to Slack
python main.py scan --slack

# Scan specific states only
python main.py scan --states TX,OK,CO,TN

# Scan Remi markets only (uses Visual Crossing for detail)
python main.py markets

# Export to CSV
python main.py scan --csv output/cat_report.csv

# Show active NWS alerts for states with CAT risk
python main.py alerts

# Run everything (scan + markets + alerts) and post to Slack
python main.py full --slack
```

## Slack Output Format

### Daily Summary (posted every 4 hours or on-demand)

```
🌩️ REMI CAT TRACKER — Feb 27, 2026 @ 2:00 PM CT

📅 DAY 1 (Today)
🔴 MODERATE RISK: Central OK, North TX (47 counties)
   → Hail: 45% | Tornado: 15% | Wind: 30%
   → Counties: Oklahoma County, Tulsa County, Dallas County...
🟠 ENHANCED RISK: Southern KS, AR (31 counties)
   → Hail: 30% | Wind: 15%

📅 DAY 2 (Tomorrow)
🟡 SLIGHT RISK: TN, MS, AL (62 counties)
   → Hail: 15% | Wind: 15%

📅 DAYS 3-5
🟡 SLIGHT: Southern Plains (Day 3)
⚪ No significant risk Days 4-5

📅 DAYS 6-8
⚪ Predictability too low / No significant risk

---
🏢 REMI MARKETS
• Dallas-Fort Worth: 🔴 MODERATE — severerisk: 78/100
• Oklahoma City: 🔴 MODERATE — severerisk: 82/100
• Houston: 🟢 CLEAR — severerisk: 12/100
• Denver: 🟢 CLEAR — severerisk: 8/100
• Nashville: 🟡 SLIGHT — severerisk: 41/100

⚠️ ACTIVE ALERTS: 3 Tornado Watches, 12 Severe T-Storm Warnings in OK/TX
```

## Dependencies

```
# requirements.txt
requests>=2.31.0
shapely>=2.0.0
geopandas>=0.14.0        # For loading county boundaries
python-dotenv>=1.0.0     # For env vars
rich>=13.0.0             # For pretty console output
```

Note: `geopandas` pulls in `fiona` and `pyproj`. If install is painful, fall back to raw `shapely` + `json` for GeoJSON parsing (skip the geopandas dependency).

## Scheduling

For MVP: run manually or via cron.

```bash
# Cron: run every 4 hours, post to Slack
0 */4 * * * cd /path/to/cat-tracker && python main.py full --slack
```

For production: move to a scheduled task on Railway, Render, or GitHub Actions.

## What Success Looks Like

1. We can see at a glance which states/counties have CAT risk in the next 8 days
2. The Slack channel gives the team a daily heads-up before storms hit
3. We start pre-positioning outreach in post-storm markets before competitors react
4. The whole thing runs on free APIs and takes < 30 seconds per scan

## What This Is NOT

- Not a real-time alerting system (that's a different tool)
- Not integrated into outreach automation yet (that's phase 2)
- Not a forecasting model (we're consuming SPC/NWS forecasts, not making our own)
