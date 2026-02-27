---
title: "Core Weather Pipeline — SPC Fetcher through Console Output"
type: feat
status: completed
date: 2026-02-27
---

# Core Weather Pipeline — Steps 1-5

## Overview

Build the core data pipeline for the Remi CAT Event Tracker: fetch SPC convective outlooks, match risk polygons to US counties, classify severity, and render results to the console. This covers Steps 1-5 from CLAUDE.md plus scaffolding (config, requirements, directory structure) and a minimal CLI orchestrator to wire everything together.

**Scope:** SPC Fetcher → County Loader → Matcher → Classifier → Console Output → Minimal CLI
**Out of scope:** Slack, NWS alerts, Visual Crossing, CSV export (Steps 6-10)

## SpecFlow Analysis Findings

A thorough flow analysis identified these critical gaps (incorporated throughout the plan):

1. **SPC probabilistic LABEL encoding is ambiguous.** Probabilistic GeoJSON may use decimal strings (`"0.15"`) rather than integer strings (`"15"`). The parser must detect format at runtime and normalize to integer percentages.
2. **"Significant severe" (SIGN) hatched areas** appear as separate features with `LABEL: "SIGN"`, not as properties on probability features. Must be handled as distinct concept.
3. **Day 3 probabilistic** may combine all hazard types into one file (unlike Days 1-2 which have separate hail/tornado/wind). Need runtime detection.
4. **Day 4-8 outlooks are "any severe" probability** — no hazard breakdown. Classifier needs a separate threshold for combined severe probability.
5. **plotly GeoJSON lacks county names** — only FIPS codes in `id` field. Resolved via bundled FIPS-to-name lookup.
6. **"No data" vs "CLEAR" distinction.** When SPC URLs all 404, we must NOT display "All Clear" — display "No SPC data available" instead.
7. **Non-CONUS counties** (Alaska, Hawaii, territories) should be filtered at load time — SPC only covers CONUS.
8. **SPC issuance time** not tracked in MVP — always fetches latest available outlook.

## Problem Statement

Remi (roofing company) discovers storm damage reactively. By the time they mobilize subcontractors and launch outreach, competitors are already in the market. The SPC issues convective outlooks 1-8 days ahead covering the entire CONUS — if we can translate those risk polygons into "which states and counties are at risk," Remi gains a 2-4 week head start on post-storm demand.

No tool currently exists that does this translation in a lightweight, CLI-friendly way. The SPC publishes polygons; we need county-level intelligence.

## Proposed Solution

A stateless Python CLI pipeline:

```
SPC GeoJSON → Risk Polygons → County Centroids Match → CAT Classification → Rich Console Output
```

Each stage is a standalone module with its own `__main__` block for testing. Data flows through dataclasses — no database, no state between runs.

## Technical Approach

### Architecture

```
weatherchaser/
├── config.py              # Constants: thresholds, markets, FIPS lookup
├── main.py                # CLI orchestrator (argparse, minimal for now)
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables (git-ignored)
├── sources/
│   ├── __init__.py
│   └── spc.py             # Step 1: Fetch + parse SPC convective outlooks
├── geo/
│   ├── __init__.py
│   ├── counties.py        # Step 2: County boundary loader + centroid computation
│   └── matcher.py         # Step 3: Point-in-polygon matching
├── classifier.py          # Step 4: Apply CAT thresholds
├── output/
│   ├── __init__.py
│   └── console.py         # Step 5: Rich console renderer
└── data/
    └── (us_counties.geojson cached here on first run)
```

### Data Flow Diagram

```
┌─────────────────────┐      ┌──────────────────────┐
│   SPC GeoJSON APIs  │      │  Plotly County GeoJSON│
│  (15 URLs, Days 1-8)│      │  (~25MB, cached)      │
└────────┬────────────┘      └──────────┬───────────┘
         │                              │
         ▼                              ▼
┌─────────────────────┐      ┌──────────────────────┐
│  sources/spc.py     │      │  geo/counties.py     │
│  → list[RiskPolygon]│      │  → list[County]      │
└────────┬────────────┘      └──────────┬───────────┘
         │                              │
         └──────────┬───────────────────┘
                    ▼
         ┌──────────────────────┐
         │  geo/matcher.py      │
         │  → list[CountyRisk]  │
         │  (per day)           │
         └──────────┬───────────┘
                    ▼
         ┌──────────────────────┐
         │  classifier.py       │
         │  → list[DayResult]   │
         │  (classified + grouped)
         └──────────┬───────────┘
                    ▼
         ┌──────────────────────┐
         │  output/console.py   │
         │  → Rich terminal     │
         └──────────────────────┘
```

### Core Data Structures

All defined in a shared location (top of `config.py` or a small `models.py` — but per CLAUDE.md "no classes unless genuinely needed," we'll put them in `config.py` alongside the constants since they're small).

```python
# config.py

from dataclasses import dataclass, field
from shapely.geometry import Polygon, MultiPolygon, Point
from typing import Union

# --- Dataclasses ---

@dataclass
class RiskPolygon:
    """A single risk area from an SPC outlook."""
    geometry: Union[Polygon, MultiPolygon]
    day: int                    # 1-8
    outlook_type: str           # "categorical", "hail", "tornado", "wind", "probabilistic"
    label: str                  # Raw LABEL from GeoJSON: "MRGL", "SLGT", "ENH", "MDT", "HIGH", "5", "15", etc.
    risk_level: int             # Normalized 0-6 for categorical; raw percent for probabilistic
    stroke: str = ""            # Hex color from GeoJSON
    fill: str = ""              # Hex color from GeoJSON

@dataclass
class County:
    """A US county with its centroid for fast matching."""
    fips: str                   # 5-digit FIPS code (zero-padded)
    name: str                   # County name
    state_fips: str             # 2-digit state FIPS
    state_abbr: str             # "TX", "OK", etc.
    centroid: Point             # Shapely Point for matching

@dataclass
class CountyRisk:
    """Risk assessment for a single county on a single day."""
    county: County
    day: int
    categorical_level: int = 0  # 0-6 (0=TSTM/none, 2=MRGL, 3=SLGT, 4=ENH, 5=MDT, 6=HIGH)
    hail_prob: int = 0          # Percentage (0-60+)
    tornado_prob: int = 0       # Percentage (0-60+)
    wind_prob: int = 0          # Percentage (0-60+)
    significant: bool = False   # True if in hatched "significant severe" area

@dataclass
class DayResult:
    """Classified results for a single forecast day."""
    day: int
    county_risks: list          # list[CountyRisk] — only counties meeting thresholds
    state_summaries: dict = field(default_factory=dict)
    # state_abbr -> {"count": int, "highest_risk": str, "counties": list[CountyRisk]}

# --- Constants ---

# SPC categorical risk labels → numeric level
SPC_RISK_LEVELS: dict[str, int] = {
    "TSTM": 1,
    "MRGL": 2,
    "SLGT": 3,
    "ENH": 4,
    "MDT": 5,
    "HIGH": 6,
}

# Reverse: level → display name
RISK_NAMES: dict[int, str] = {
    0: "NONE",
    1: "TSTM",
    2: "MARGINAL",
    3: "SLIGHT",
    4: "ENHANCED",
    5: "MODERATE",
    6: "HIGH",
}

# CAT classification thresholds
CAT_THRESHOLDS = {
    "spc_categorical_min": 3,   # ENH or higher
    "hail_prob_min": 15,        # 15%+
    "tornado_prob_min": 5,      # 5%+
    "wind_prob_min": 15,        # 15%+
}

# Remi markets (for future Visual Crossing integration)
REMI_MARKETS = [
    {"name": "Dallas-Fort Worth", "zip": "75201", "state": "TX"},
    {"name": "Houston", "zip": "77001", "state": "TX"},
    {"name": "Denver", "zip": "80201", "state": "CO"},
    {"name": "Oklahoma City", "zip": "73101", "state": "OK"},
    {"name": "Nashville", "zip": "37201", "state": "TN"},
]

# FIPS state codes → 2-letter abbreviation
# Full 50 states + DC. Territories omitted (SPC covers CONUS only).
STATE_FIPS: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}
```

### Implementation Phases

---

#### Phase 0: Project Scaffolding

**Files:** `config.py`, `requirements.txt`, `.env`, `__init__.py` files, `data/` directory

**Tasks:**
- [x] Create directory structure: `sources/`, `geo/`, `output/`, `data/`
- [x] Create `__init__.py` for each package (empty files)
- [x] Write `requirements.txt` with pinned dependencies
- [x] Write `config.py` with dataclasses, constants, thresholds, FIPS lookup table
- [x] Create `.env` template and `.gitignore` (exclude `data/`, `.env`, `__pycache__/`, `venv/`)

**`requirements.txt`:**
```
requests>=2.31.0
shapely>=2.0.0
python-dotenv>=1.0.0
rich>=13.0.0
```

> **Decision: Skip geopandas.** The plotly GeoJSON can be parsed with `json` + `shapely` directly. Avoiding geopandas means no fiona/pyproj/GDAL install pain. We only need polygon centroids — Shapely handles this natively.

**Success criteria:**
- `python -c "import config"` works without error
- All directories exist

---

#### Phase 1: SPC Outlook Fetcher (`sources/spc.py`)

**Purpose:** Fetch all SPC convective outlook GeoJSON files and parse into `RiskPolygon` dataclasses.

**SPC URL matrix (15 total fetches):**

| Day | Categorical | Hail | Tornado | Wind | Probabilistic |
|-----|------------|------|---------|------|---------------|
| 1   | ✓          | ✓    | ✓       | ✓    |               |
| 2   | ✓          | ✓    | ✓       | ✓    |               |
| 3   | ✓          |      |         |      | ✓             |
| 4-8 |            |      |         |      | ✓ each        |

**Key implementation details:**

1. **URL construction:** Build URL list from day number + type. Store URL patterns as constants.

2. **GeoJSON parsing:** Each feature has:
   - `geometry`: Polygon or MultiPolygon (handle both via `shape()`)
   - `properties.LABEL`: Risk category string
     - Categorical: `"TSTM"`, `"MRGL"`, `"SLGT"`, `"ENH"`, `"MDT"`, `"HIGH"`
     - Probabilistic: `"5"`, `"15"`, `"30"`, `"45"`, `"60"` (percent as string)
     - Significant: `"SIGN"` or `"SIG"` for hatched significant severe areas
   - `properties.fill`: Hex color string
   - `properties.stroke`: Hex color string

3. **Label → risk_level mapping:**
   - Categorical: use `SPC_RISK_LEVELS` dict lookup (e.g., `"ENH"` → 4)
   - Probabilistic: **format detection required** — LABEL may be:
     - Integer string: `"15"` → 15%
     - Decimal string: `"0.15"` → 15% (multiply by 100)
     - Parser should try `float(label)`: if ≤ 1.0, treat as fraction and multiply by 100; if > 1, treat as percentage
   - `"SIGN"` / `"SIG"`: flag as `significant=True` on the polygon, don't treat as a risk level. These appear as **separate features** in the GeoJSON (not properties on other features)

4. **Error handling:**
   - HTTP 404: expected for Days 4-8 with no significant risk. Log to stderr, skip.
   - HTTP 200 with empty FeatureCollection: valid "no risk" response. Not an error.
   - HTTP 200 with non-JSON content (SPC maintenance page): catch `JSONDecodeError`, skip with warning.
   - HTTP timeout (30s): retry once after 2-second delay, then skip with warning to stderr.
   - HTTP 5xx: retry once after 2-second delay, then skip.
   - HTTP 4xx (except 404): skip with warning, do not retry.
   - Connection error: retry once after 2-second delay, then skip.
   - **Track fetch success/failure** — if ALL URLs fail, the classifier must know the difference between "no risk" and "no data."

5. **Return type:** `dict[int, list[RiskPolygon]]` — keyed by day number.

**Functions:**

```python
def fetch_spc_outlooks() -> dict[int, list[RiskPolygon]]:
    """Fetch all SPC outlook GeoJSON files for Days 1-8."""

def _fetch_geojson(url: str) -> dict | None:
    """Fetch a single GeoJSON URL with retry. Returns None on failure."""

def _parse_features(geojson: dict, day: int, outlook_type: str) -> list[RiskPolygon]:
    """Parse GeoJSON features into RiskPolygon objects."""

def _label_to_risk_level(label: str, outlook_type: str) -> int:
    """Convert LABEL string to numeric risk level."""
```

**Standalone test (`python -m sources.spc`):**
```
Fetching SPC outlooks...
  Day 1 categorical: 5 polygons
  Day 1 hail: 3 polygons
  Day 1 tornado: 2 polygons
  Day 1 wind: 3 polygons
  Day 2 categorical: 4 polygons
  ...
  Day 5 probabilistic: 404 (no outlook)
  ...
Total: 28 risk polygons across 8 days
```

**Success criteria:**
- Fetches live SPC data without crashing
- Handles 404s for missing days gracefully (stderr warning, not exception)
- Correctly parses both Polygon and MultiPolygon geometries
- `LABEL` values correctly mapped to risk levels

---

#### Phase 2: County Boundary Loader (`geo/counties.py`)

**Purpose:** Download (once) and parse US county boundaries, computing centroids for fast matching.

**Key implementation details:**

1. **Download + cache:**
   - Source: `https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json`
   - Save to `data/us_counties.geojson`
   - Skip download if file exists (check `os.path.exists`)
   - Use `requests.get()` with 60-second timeout (file is ~25MB)
   - Print download progress to stderr

2. **The FIPS-to-name problem:**
   The plotly GeoJSON only has `id` (FIPS code) and `geometry` — no county name or state name in properties. Solutions:
   - **State:** Extract from first 2 digits of FIPS using `STATE_FIPS` lookup in `config.py`
   - **County name:** The plotly dataset doesn't include names. Options:
     - **(a) Accept no names** — use FIPS codes only in output. Functional but ugly.
     - **(b) Bundle a small FIPS→name CSV** — adds a file but gives clean output.
     - **(c) Use Census Bureau TIGER data instead** — has names but is a shapefile (needs fiona/geopandas).

   **Decision: Option (b).** Generate a `data/fips_names.csv` file from the Census Bureau FIPS code list. This is a small (~150KB) static file with columns: `fips,name,state`. We can download it once from Census or bundle it. This avoids geopandas entirely while still giving us county names.

   Alternatively, the plotly GeoJSON `properties` object may contain fields we can use. During Phase 1 implementation, we should inspect the actual data and fall back to FIPS-only if no name field exists.

   **Pragmatic fallback:** If generating the names file proves complex, use the [FIPS code → Census data API](https://api.census.gov/data/2020/dec/pl?get=NAME&for=county:*) to build it once, or hard-code "County {FIPS}" as placeholder names and revisit.

3. **Centroid computation:**
   ```python
   from shapely.geometry import shape
   geometry = shape(feature["geometry"])
   centroid = geometry.centroid  # Returns Point(lon, lat)
   ```

4. **Filter non-CONUS counties at load time:**
   SPC convective outlooks cover CONUS only. Filter out Alaska (02), Hawaii (15), and territories (60, 66, 69, 72, 78) during parsing. This saves ~200 unnecessary point-in-polygon checks per polygon and prevents non-CONUS counties from appearing in output as perpetually "CLEAR."

5. **Memory consideration:**
   ~3,143 counties × (Point + metadata) is trivial in memory. No optimization needed.

**Functions:**

```python
def load_counties() -> list[County]:
    """Load county boundaries from cached GeoJSON. Downloads if not cached."""

def _download_counties(dest: str) -> None:
    """Download county boundaries GeoJSON to dest path."""

def _parse_county_geojson(path: str) -> list[County]:
    """Parse GeoJSON file into County dataclasses with centroids."""
```

**Standalone test (`python -m geo.counties`):**
```
Loading county boundaries...
  Downloading county boundaries (first run)... done (25.2 MB)
  Parsed 3,143 counties across 50 states + DC
  Sample: 48201 (Harris County, TX) centroid: (-95.39, 29.76)
  Sample: 36061 (New York County, NY) centroid: (-73.97, 40.78)
```

**Success criteria:**
- Downloads GeoJSON on first run, skips on subsequent runs
- Parses all ~3,143 counties
- State FIPS correctly mapped to abbreviations
- Centroids are valid Shapely Points within CONUS bounds

---

#### Phase 3: Geographic Matcher (`geo/matcher.py`)

**Purpose:** Match county centroids against SPC risk polygons to determine which counties are at risk.

**Key implementation details:**

1. **Matching algorithm:**
   ```
   For each day:
     For each risk polygon for that day:
       bbox = polygon.bounds  # (minx, miny, maxx, maxy)
       For each county:
         if centroid is NOT within bbox: skip (fast rejection)
         if centroid.within(polygon): record match
   ```

2. **Bounding box optimization:**
   The `polygon.bounds` property returns `(minlon, minlat, maxlon, maxlat)`. Checking if a point is within the bounding box is O(1) and eliminates ~90% of counties before the expensive `within()` call.

   ```python
   minx, miny, maxx, maxy = polygon.bounds
   cx, cy = centroid.x, centroid.y
   if cx < minx or cx > maxx or cy < miny or cy > maxy:
       continue  # Fast rejection
   ```

3. **Handling overlapping polygons:**
   A county centroid may fall inside multiple risk polygons (e.g., both SLGT and ENH). The matcher must:
   - Track ALL matching overlaps per county per day
   - For categorical: keep the **highest** risk level
   - For probabilistic: keep the **highest** percentage for each type (hail/tornado/wind)
   - For significant: set flag if county is in ANY significant area

4. **Handling MultiPolygon:**
   ```python
   from shapely.geometry import shape, MultiPolygon
   geom = risk_polygon.geometry
   if isinstance(geom, MultiPolygon):
       sub_polygons = list(geom.geoms)
   else:
       sub_polygons = [geom]
   for poly in sub_polygons:
       # check centroid.within(poly)
   ```

5. **State-level aggregation:**
   After matching all counties, group by `county.state_abbr`:
   ```python
   state_summaries = defaultdict(list)
   for cr in county_risks:
       state_summaries[cr.county.state_abbr].append(cr)
   ```

6. **Performance estimate:**
   - ~3,143 counties × ~15-30 polygons × 8 days = ~375K-750K bbox checks
   - Bbox rejects ~90% → ~37K-75K `within()` calls
   - Each `within()` is ~1ms → ~37-75 seconds worst case
   - **Optimization if too slow:** Use Shapely's `STRtree` spatial index on county centroids:
     ```python
     from shapely import STRtree
     tree = STRtree([c.centroid for c in counties])
     matches = tree.query(polygon, predicate="within")
     ```
     This reduces matching to ~O(n log n) and should complete in <5 seconds. **Implement STRtree from the start** — it's 3 lines of code and avoids performance issues.

**Functions:**

```python
def match_counties(
    outlooks: dict[int, list[RiskPolygon]],
    counties: list[County],
) -> dict[int, list[CountyRisk]]:
    """Match county centroids to SPC risk polygons. Returns day -> county risks."""

def _merge_risks(existing: CountyRisk, polygon: RiskPolygon) -> CountyRisk:
    """Merge a new polygon match into an existing county risk (keep highest)."""

def _aggregate_by_state(county_risks: list[CountyRisk]) -> dict[str, dict]:
    """Group county risks by state abbreviation."""
```

**Standalone test (`python -m geo.matcher`):**
```
Loading SPC outlooks...
Loading county boundaries...
Matching counties to risk polygons...
  Day 1: 147 counties in 8 states
  Day 2: 83 counties in 5 states
  Day 3: 62 counties in 4 states
  Day 4-8: no significant risk
States at risk: TX (47), OK (31), KS (22), ...
```

**Success criteria:**
- Correctly matches counties within SPC polygons
- Highest risk level wins when polygons overlap
- Bounding box pre-filter works (verify with timing)
- State aggregation produces reasonable results
- Handles empty results (no risk anywhere) gracefully

---

#### Phase 4: Classifier (`classifier.py`)

**Purpose:** Apply CAT thresholds from `config.py` to matched county risks and produce structured results for output.

**Key implementation details:**

1. **Threshold application:**
   A county is flagged as "CAT risk" if ANY of:
   - Categorical level ≥ `CAT_THRESHOLDS["spc_categorical_min"]` (ENH = 4)
   - Hail probability ≥ `CAT_THRESHOLDS["hail_prob_min"]` (15%)
   - Tornado probability ≥ `CAT_THRESHOLDS["tornado_prob_min"]` (5%)
   - Wind probability ≥ `CAT_THRESHOLDS["wind_prob_min"]` (15%)
   - `significant` flag is True

2. **Classification labels:**
   Each county gets a display classification based on its highest categorical level:
   - 0-1: CLEAR / TSTM (below threshold — included in data but marked as no-action)
   - 2: MARGINAL
   - 3: SLIGHT
   - 4: ENHANCED
   - 5: MODERATE
   - 6: HIGH

   Counties can also be flagged purely from probabilistic thresholds even if categorical is low. Example: a county at MRGL categorical but with 20% hail probability should still be flagged.

   **Day 3 probabilistic handling:** The Day 3 `prob` file may combine all hazard types. If features distinguish hazard type via a property field, separate them. If not, treat as "any severe" and compare against the lowest individual threshold (5%, the tornado threshold).

   **Day 4-8 handling:** These are "any severe" probability only — no hazard breakdown. Use a dedicated threshold: 15% "any severe" probability = flag as CAT risk. Display as "Severe Risk: X%" without hail/tornado/wind breakdown.

3. **Output structure:**
   Returns `list[DayResult]` with only days that have risk (skip days with no matches). Each `DayResult` contains:
   - Filtered `county_risks` (only those meeting thresholds)
   - `state_summaries` dict with per-state aggregation
   - Counties sorted by risk level (highest first)

4. **Edge case: "all clear" vs "no data":**
   - If SPC data was fetched successfully but no counties meet thresholds → "all clear" result. Display "No significant severe weather risk in the next 8 days."
   - If ALL SPC fetches failed (every URL returned error/404) → "no data" result. Display a prominent warning: "WARNING: No SPC outlook data available. Cannot determine risk." Do NOT display "All Clear" without data to back it up.
   - Track this via a `data_available: bool` flag passed from the fetcher through the pipeline.

**Functions:**

```python
def classify(
    matched: dict[int, list[CountyRisk]],
) -> list[DayResult]:
    """Apply CAT thresholds and return classified results per day."""

def _meets_threshold(risk: CountyRisk) -> bool:
    """Check if a county risk meets any CAT threshold."""

def _risk_display_name(level: int) -> str:
    """Convert numeric risk level to display string."""
```

**Standalone test (`python -m classifier`):**
```
Running full pipeline: fetch → match → classify...
Day 1: 47 counties flagged (ENHANCED: 12, MODERATE: 8, HIGH: 2, threshold-only: 25)
Day 2: 31 counties flagged (SLIGHT: 22, ENHANCED: 9)
Day 3: no counties above threshold
```

**Success criteria:**
- Counties below all thresholds are excluded from results
- Counties meeting probabilistic thresholds but low categorical are still included
- Significant severe flag works correctly
- Results sorted by risk level descending
- Empty/all-clear case handled

---

#### Phase 5: Console Output (`output/console.py`)

**Purpose:** Render classified results as a rich, color-coded terminal display using the `rich` library.

**Key implementation details:**

1. **Color scheme:**
   | Risk Level | Rich Color | Emoji |
   |-----------|-----------|-------|
   | HIGH      | bold red  | 🔴    |
   | MODERATE  | red       | 🔴    |
   | ENHANCED  | orange3   | 🟠    |
   | SLIGHT    | yellow    | 🟡    |
   | MARGINAL  | cyan      | 🔵    |
   | CLEAR     | green     | 🟢    |

2. **Layout (matches Slack format from spec):**
   ```
   ═══════════════════════════════════════════════════
    REMI CAT TRACKER — Feb 27, 2026 @ 2:00 PM CT
   ═══════════════════════════════════════════════════

   📅 DAY 1 (Today - Thu Feb 27)
   ┌─────────────────────────────────────────────────┐
   │ 🔴 MODERATE RISK                                │
   │    Central OK, North TX (47 counties)           │
   │    Hail: 45% │ Tornado: 15% │ Wind: 30%        │
   │    Top counties: Oklahoma, Tulsa, Dallas...     │
   ├─────────────────────────────────────────────────┤
   │ 🟠 ENHANCED RISK                                │
   │    Southern KS, AR (31 counties)                │
   │    Hail: 30% │ Wind: 15%                        │
   └─────────────────────────────────────────────────┘

   📅 DAY 2 (Tomorrow - Fri Feb 28)
   ┌─────────────────────────────────────────────────┐
   │ 🟡 SLIGHT RISK                                  │
   │    TN, MS, AL (62 counties)                     │
   │    Hail: 15% │ Wind: 15%                        │
   └─────────────────────────────────────────────────┘

   📅 DAYS 3-8
   ⚪ No significant risk

   ═══════════════════════════════════════════════════
   Scan complete. 3 days with CAT-level risk.
   ═══════════════════════════════════════════════════
   ```

3. **Rich components to use:**
   - `Console` for output
   - `Panel` for day groupings
   - `Table` for state/county summaries (if many results)
   - `Text` with styles for color-coding
   - `Rule` for header/footer separators

4. **Grouping logic:**
   - Group by day (ascending)
   - Within each day, group by risk level (descending — highest first)
   - Within each risk level, list states with county counts
   - Show top 5 county names per state, then "... and N more"

5. **"All clear" output:**
   ```
   ═══════════════════════════════════════════════════
    REMI CAT TRACKER — Feb 27, 2026 @ 2:00 PM CT
   ═══════════════════════════════════════════════════

   🟢 No significant severe weather risk in the next 8 days.
   All clear across CONUS.
   ```

6. **Stderr vs stdout:**
   - Progress messages ("Fetching SPC outlooks...", "Loading counties...") → stderr
   - Final rendered output → stdout
   - This allows piping: `python main.py scan > report.txt`

**Functions:**

```python
def render_console(results: list[DayResult]) -> None:
    """Render classified results to the terminal using Rich."""

def _render_day(console: Console, day_result: DayResult) -> None:
    """Render a single day's results."""

def _risk_style(level: int) -> str:
    """Return Rich style string for a risk level."""

def _risk_emoji(level: int) -> str:
    """Return emoji for a risk level."""
```

**Standalone test (`python -m output.console`):**
Runs the full pipeline (fetch → load → match → classify → render) and displays to terminal.

**Success criteria:**
- Output is readable and color-coded
- Days with no risk are collapsed
- State/county grouping is correct
- All-clear case renders cleanly
- Progress goes to stderr, output to stdout

---

#### Phase 6: Minimal CLI Orchestrator (`main.py`)

**Purpose:** Wire Steps 1-5 together with a basic `argparse` CLI. Only the `scan` command for now.

**Implementation:**

```python
# main.py
import argparse
import sys
from dotenv import load_dotenv

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Remi CAT Event Tracker")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Scan SPC outlooks")
    scan_parser.add_argument("--states", help="Comma-separated state codes to filter")

    args = parser.parse_args()
    if args.command == "scan":
        run_scan(states=args.states)
    else:
        parser.print_help()

def run_scan(states: str | None = None):
    from sources.spc import fetch_spc_outlooks
    from geo.counties import load_counties
    from geo.matcher import match_counties
    from classifier import classify
    from output.console import render_console

    print("Fetching SPC outlooks...", file=sys.stderr)
    outlooks = fetch_spc_outlooks()

    print("Loading county boundaries...", file=sys.stderr)
    counties = load_counties()

    if states:
        state_filter = {s.strip().upper() for s in states.split(",")}
        counties = [c for c in counties if c.state_abbr in state_filter]
        print(f"Filtering to {len(counties)} counties in {state_filter}", file=sys.stderr)

    print("Matching counties to risk areas...", file=sys.stderr)
    matched = match_counties(outlooks, counties)

    print("Classifying results...", file=sys.stderr)
    results = classify(matched)

    render_console(results)
```

**Flags for later steps (stubbed but not wired):**
- `--slack` — Step 6
- `--csv` — Step 9
- `markets` and `alerts` subcommands — Steps 7-8

**Success criteria:**
- `python main.py scan` runs the full pipeline
- `python main.py scan --states TX,OK` filters correctly
- `python main.py` (no args) shows help

---

## Alternative Approaches Considered

### 1. geopandas vs raw Shapely + json

**Considered:** Using geopandas to load and manipulate GeoJSON files.
**Rejected:** geopandas pulls in fiona, pyproj, and potentially GDAL — heavy dependencies that can be painful to install. Since we only need point-in-polygon checks on pre-loaded GeoJSON, `json.load()` + `shapely.geometry.shape()` is sufficient and avoids the dependency hell.

### 2. Full polygon intersection vs centroid matching

**Considered:** Checking if county polygons intersect SPC risk polygons (full geometry intersection).
**Rejected:** Much slower and not meaningfully more accurate for our use case. If a county centroid is inside a risk polygon, the county is at risk. Edge cases where a county border touches a risk polygon but the centroid doesn't are not operationally important.

### 3. Census TIGER shapefiles vs plotly GeoJSON

**Considered:** Using Census TIGER county shapefiles which include county names and state names.
**Rejected for Phase 1:** Requires fiona to parse shapefiles, which pulls in GDAL. The plotly GeoJSON is pure JSON. We'll supplement with a FIPS-to-name lookup instead. Can revisit if the plotly data is insufficient.

### 4. Spatial indexing library (rtree) vs Shapely STRtree

**Considered:** Using the `rtree` library for spatial indexing.
**Rejected:** Shapely 2.0+ includes `STRtree` natively — no additional dependency needed. Performance is comparable for our data sizes.

## System-Wide Impact

### Interaction Graph

```
main.py scan
  → sources/spc.fetch_spc_outlooks()
    → 15 HTTP requests to spc.noaa.gov (GeoJSON)
    → Returns dict[int, list[RiskPolygon]]
  → geo/counties.load_counties()
    → Checks data/us_counties.geojson exists
    → If not: 1 HTTP request to plotly/datasets (25MB download)
    → Parses JSON, computes Shapely centroids
    → Returns list[County]
  → geo/matcher.match_counties(outlooks, counties)
    → Builds STRtree spatial index from county centroids
    → For each polygon: query tree, merge results
    → Returns dict[int, list[CountyRisk]]
  → classifier.classify(matched)
    → Filters by thresholds, sorts, groups
    → Returns list[DayResult]
  → output/console.render_console(results)
    → Rich console output to stdout
```

No callbacks, no middleware, no observers, no event handlers. Pure functional pipeline. Each step receives data and returns data. No side effects except HTTP fetches and stdout/stderr.

### Error & Failure Propagation

| Error Source | Error Type | Handling | Impact |
|-------------|-----------|----------|--------|
| SPC 404 | `requests.HTTPError` | Log warning to stderr, skip that URL | Partial results (fewer days) |
| SPC timeout | `requests.Timeout` | Retry once, then skip with warning | Partial results |
| SPC bad JSON | `json.JSONDecodeError` | Skip with warning | Partial results |
| County download fail | `requests.RequestException` | Fatal — exit with error message | Cannot proceed without county data |
| County bad JSON | `json.JSONDecodeError` | Fatal — exit with error | Cannot proceed |
| Shapely geometry error | `GEOSException` | Log warning, skip that polygon | Minor data loss |
| No results at all | N/A | Display "all clear" message | Expected case |

**Key principle:** SPC failures are tolerable (we get partial data). County data failure is fatal (we can't match anything without counties).

### State Lifecycle Risks

None. This is a stateless tool. No database, no cache (except the county GeoJSON file which is idempotent), no mutable state between runs. Each run is independent.

The only persistent state is `data/us_counties.geojson`. If corrupted, delete it and re-run — it will re-download.

### API Surface Parity

For Steps 1-5, the only interface is `main.py scan [--states STATES]`. Future steps add `--slack`, `--csv`, `markets`, `alerts`, and `full` commands. The internal function interfaces (`fetch_spc_outlooks`, `load_counties`, `match_counties`, `classify`, `render_console`) form the pipeline API that all output formatters will consume.

**Design for extensibility:** The `list[DayResult]` output from `classify()` is the shared contract. Console, Slack, and CSV outputs all consume this same structure. No output-specific logic in the classifier.

### Integration Test Scenarios

1. **Full pipeline with live data:** `python main.py scan` — fetches real SPC data, loads counties, matches, classifies, renders. Verify output is reasonable for current weather conditions.
2. **State filter:** `python main.py scan --states TX,OK` — verify only TX/OK counties appear in output.
3. **Quiet weather day:** Run when SPC shows low risk — verify "all clear" or minimal results render correctly.
4. **First-run county download:** Delete `data/us_counties.geojson`, run pipeline, verify it re-downloads and caches.
5. **Partial SPC failure:** Simulate by checking behavior when some Day 4-8 URLs 404 — verify other days still work.

## Acceptance Criteria

### Functional Requirements

- [ ] `python main.py scan` runs end-to-end and produces readable output
- [ ] SPC GeoJSON data for Days 1-8 is fetched and parsed correctly
- [ ] Both Polygon and MultiPolygon geometries are handled
- [ ] County boundaries (~3,143) are loaded with centroids
- [ ] Counties correctly matched to overlapping risk polygons (highest risk wins)
- [ ] CAT thresholds applied: ENH+ categorical, hail≥15%, tornado≥5%, wind≥15%
- [ ] Console output grouped by day (ascending) then risk level (descending)
- [ ] Color-coded output using Rich
- [ ] `--states` filter works correctly
- [ ] Each module runnable standalone via `python -m <module>`
- [ ] Graceful handling of 404s for Day 4-8 outlooks
- [ ] "All clear" message when no risk areas meet thresholds
- [ ] "No data available" warning when ALL SPC fetches fail (not false "all clear")
- [ ] Non-CONUS counties (AK, HI, territories) excluded from results
- [ ] SPC probabilistic LABEL values parsed correctly regardless of format (decimal or integer strings)
- [ ] SIGN/SIG hatched areas detected and flagged as significant severe
- [ ] Day 4-8 "any severe" probability classified with appropriate threshold

### Non-Functional Requirements

- [ ] Full pipeline completes in <60 seconds (network permitting)
- [ ] County matching completes in <10 seconds (STRtree index)
- [ ] No geopandas / GDAL dependency — only requests, shapely, python-dotenv, rich
- [ ] All API calls have 30-second timeout
- [ ] Progress messages to stderr, results to stdout
- [ ] Python 3.10+ with type hints on all function signatures
- [ ] Dataclasses for structured data (not plain dicts)

### Quality Gates

- [ ] `python -m sources.spc` outputs polygon counts per day/type
- [ ] `python -m geo.counties` outputs ~3,143 counties with sample records
- [ ] `python -m geo.matcher` outputs matched counties per day/state
- [ ] `python -m classifier` outputs classified results above threshold
- [ ] `python main.py scan` produces full color-coded console output

## Success Metrics

1. **Pipeline runs end-to-end** on live SPC data without manual intervention
2. **Results match reality** — spot-check SPC website and verify our output matches
3. **< 60 second runtime** for full CONUS scan
4. **Clean install** — `pip install -r requirements.txt` succeeds on Mac/Linux without GDAL/fiona pain
5. **Readable output** — a non-technical Remi team member can understand the console output

## Dependencies & Prerequisites

### External Dependencies (pip)

| Package | Version | Purpose |
|---------|---------|---------|
| requests | >=2.31.0 | HTTP client for SPC/county data |
| shapely | >=2.0.0 | Geometry operations, spatial index |
| python-dotenv | >=1.0.0 | Load .env for future Slack/VC keys |
| rich | >=13.0.0 | Terminal formatting and colors |

### External Services

| Service | Auth | Rate Limit | Reliability |
|---------|------|-----------|-------------|
| SPC (spc.noaa.gov) | None | None known | High — government service |
| Plotly GitHub (raw.githubusercontent.com) | None | GitHub rate limits | High — static file |

### System Requirements

- Python 3.10+
- Internet access (for SPC data + first-run county download)
- ~30MB disk for cached county GeoJSON

## Risk Analysis & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| SPC URL format changes | Low | High | Pin URLs as constants; easy to update |
| SPC GeoJSON field names change | Low | High | All field access through parse functions; centralized |
| Plotly county GeoJSON removed | Low | High | Cache locally; can switch to Census TIGER as fallback |
| Shapely 2.0 API changes | Low | Medium | Pin `>=2.0.0`; STRtree API is stable |
| SPC outage during scan | Medium | Low | Retry + skip pattern; partial results still useful |
| County matching too slow | Low | Medium | STRtree spatial index handles this |
| plotly GeoJSON missing county names | High | Low | FIPS lookup fallback; names are nice-to-have |

## Future Considerations

Steps 1-5 are designed to support Steps 6-10 without restructuring:

- **Step 6 (Slack):** Consumes `list[DayResult]` — same data as console, different formatter
- **Step 7 (NWS Alerts):** Independent data source; results can be appended to `DayResult` or rendered separately
- **Step 8 (Visual Crossing):** Independent; adds market-level detail alongside county data
- **Step 9 (CSV):** Flattens `list[DayResult]` into rows — straightforward
- **Step 10 (Full CLI):** Adds subcommands and flags to existing `main.py`

The `DayResult` dataclass is the shared contract. It may need a `market_details` or `alerts` field added in future steps, but the core structure (day → counties → risk levels) won't change.

## Sources & References

### Internal References

- Spec: `spec.md` — full requirements, data sources, Slack format, CLI interface
- Implementation guide: `CLAUDE.md` — step-by-step build order, code style, gotchas
- Setup: `INSTRUCTIONS.md` — quick start, API key info

### External References

- SPC Convective Outlook Products: https://www.spc.noaa.gov/products/outlook/
- SPC GeoJSON format: URLs documented in `spec.md` lines 30-40
- Plotly county boundaries: https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json
- Shapely 2.0 documentation: https://shapely.readthedocs.io/
- Rich library: https://rich.readthedocs.io/
- FIPS county codes: https://www.census.gov/library/reference/code-lists/ansi.html
