"""Configuration, constants, and shared data structures for the CAT Event Tracker."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Union

from shapely.geometry import MultiPolygon, Point, Polygon


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RiskPolygon:
    """A single risk area from an SPC outlook."""
    geometry: Union[Polygon, MultiPolygon]
    day: int                    # 1-8
    outlook_type: str           # "categorical", "hail", "tornado", "wind", "probabilistic"
    label: str                  # Raw LABEL from GeoJSON
    risk_level: int             # Normalized 0-6 for categorical; percent for probabilistic
    stroke: str = ""
    fill: str = ""
    significant: bool = False   # True for SIGN/SIG hatched features


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
    categorical_level: int = 0  # 0-6
    hail_prob: int = 0          # Percentage (0-60+)
    tornado_prob: int = 0       # Percentage (0-60+)
    wind_prob: int = 0          # Percentage (0-60+)
    significant: bool = False   # True if in hatched "significant severe" area


@dataclass
class DayResult:
    """Classified results for a single forecast day."""
    day: int
    county_risks: list[CountyRisk] = field(default_factory=list)
    state_summaries: dict = field(default_factory=dict)
    # state_abbr -> {"count": int, "highest_risk": int, "counties": list[CountyRisk]}
    data_available: bool = True  # False when ALL SPC fetches for this day failed


# ---------------------------------------------------------------------------
# SPC risk level mappings
# ---------------------------------------------------------------------------

SPC_RISK_LEVELS: dict[str, int] = {
    "TSTM": 1,
    "MRGL": 2,
    "SLGT": 3,
    "ENH": 4,
    "MDT": 5,
    "HIGH": 6,
}

RISK_NAMES: dict[int, str] = {
    0: "NONE",
    1: "TSTM",
    2: "MARGINAL",
    3: "SLIGHT",
    4: "ENHANCED",
    5: "MODERATE",
    6: "HIGH",
}

# ---------------------------------------------------------------------------
# CAT classification thresholds
# ---------------------------------------------------------------------------

CAT_THRESHOLDS = {
    "spc_categorical_min": 4,   # ENH (level 4) or higher
    "hail_prob_min": 15,        # 15%+
    "tornado_prob_min": 5,      # 5%+
    "wind_prob_min": 15,        # 15%+
    "any_severe_prob_min": 15,  # For Day 4-8 combined probability
}

# ---------------------------------------------------------------------------
# Remi markets (for future Visual Crossing integration)
# ---------------------------------------------------------------------------

REMI_MARKETS = [
    {"name": "Dallas-Fort Worth", "zip": "75201", "state": "TX"},
    {"name": "Houston", "zip": "77001", "state": "TX"},
    {"name": "Denver", "zip": "80201", "state": "CO"},
    {"name": "Oklahoma City", "zip": "73101", "state": "OK"},
    {"name": "Nashville", "zip": "37201", "state": "TN"},
]

# ---------------------------------------------------------------------------
# FIPS state codes → 2-letter abbreviation (CONUS + DC)
# ---------------------------------------------------------------------------

STATE_FIPS: dict[str, str] = {
    "01": "AL", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}

# Non-CONUS FIPS prefixes to exclude (Alaska, Hawaii, territories)
NON_CONUS_FIPS: set[str] = {"02", "15", "60", "66", "69", "72", "78"}

# ---------------------------------------------------------------------------
# SPC URLs
# ---------------------------------------------------------------------------

SPC_BASE = "https://www.spc.noaa.gov/products/outlook"
SPC_DAY48_BASE = "https://www.spc.noaa.gov/products/exper/day4-8"

# (day, outlook_type, url)
SPC_URLS: list[tuple[int, str, str]] = [
    # Day 1
    (1, "categorical", f"{SPC_BASE}/day1otlk_cat.lyr.geojson"),
    (1, "hail",        f"{SPC_BASE}/day1otlk_hail.lyr.geojson"),
    (1, "tornado",     f"{SPC_BASE}/day1otlk_torn.lyr.geojson"),
    (1, "wind",        f"{SPC_BASE}/day1otlk_wind.lyr.geojson"),
    # Day 2
    (2, "categorical", f"{SPC_BASE}/day2otlk_cat.lyr.geojson"),
    (2, "hail",        f"{SPC_BASE}/day2otlk_hail.lyr.geojson"),
    (2, "tornado",     f"{SPC_BASE}/day2otlk_torn.lyr.geojson"),
    (2, "wind",        f"{SPC_BASE}/day2otlk_wind.lyr.geojson"),
    # Day 3
    (3, "categorical", f"{SPC_BASE}/day3otlk_cat.lyr.geojson"),
    (3, "probabilistic", f"{SPC_BASE}/day3otlk_prob.lyr.geojson"),
    # Days 4-8
    (4, "probabilistic", f"{SPC_DAY48_BASE}/day4prob.lyr.geojson"),
    (5, "probabilistic", f"{SPC_DAY48_BASE}/day5prob.lyr.geojson"),
    (6, "probabilistic", f"{SPC_DAY48_BASE}/day6prob.lyr.geojson"),
    (7, "probabilistic", f"{SPC_DAY48_BASE}/day7prob.lyr.geojson"),
    (8, "probabilistic", f"{SPC_DAY48_BASE}/day8prob.lyr.geojson"),
]

COUNTY_GEOJSON_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
COUNTY_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "us_counties.geojson")

# ---------------------------------------------------------------------------
# HTTP settings
# ---------------------------------------------------------------------------

HTTP_TIMEOUT = 30       # seconds for API calls
DOWNLOAD_TIMEOUT = 120  # seconds for large file downloads
RETRY_DELAY = 2         # seconds between retries
