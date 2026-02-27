# INSTRUCTIONS.md — Setup & Context

## Quick Start

```bash
# Create project directory
mkdir cat-tracker && cd cat-tracker

# Copy spec.md and CLAUDE.md into this directory

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install requests shapely geopandas python-dotenv rich

# Create .env file
echo "SLACK_WEBHOOK_URL=" >> .env
echo "VISUAL_CROSSING_API_KEY=" >> .env

# Create data directory for cached county boundaries
mkdir -p data

# Run first scan
python main.py scan
```

## API Keys Needed

| Service | Key Required? | How to Get |
|---------|--------------|------------|
| SPC (NOAA) | No | Free, public GeoJSON files |
| NWS Alerts | No | Free, just needs User-Agent header |
| Visual Crossing | Yes (optional) | Sign up at visualcrossing.com/sign-up, free tier = 1K records/day |
| Slack Webhook | Yes (for Slack output) | Create incoming webhook in Slack workspace settings |

## Project Context

This tool is for Remi Roofing & Construction. We're a roofing company that does work across multiple US markets. When severe weather (especially hail) hits a region, roofing demand surges 2-4 weeks later as homeowners file insurance claims and need repairs.

Right now, we find out about storm damage reactively. This tool gives us a forward-looking view so we can:

1. **Pre-position subcontractors** in markets expecting CAT events
2. **Plan outreach campaigns** timed to post-storm demand windows
3. **Brief the sales team** on where demand is about to spike
4. **Track storm patterns** across our markets over time

## Phase 2 Ideas (Not In Scope for MVP)

- Wire CAT signals into outreach automation (suppress during active storms, ramp post-storm)
- Historical storm damage tracking (NOAA storm reports database)
- Insurance claim volume correlation
- Web dashboard with map visualization
- Post-storm canvassing route optimization
- Automated subcontractor mobilization notifications
