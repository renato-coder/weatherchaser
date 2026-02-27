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
# Remi markets — metro areas mapped to county FIPS codes
# ---------------------------------------------------------------------------

@dataclass
class Market:
    """A Remi metro market defined by its constituent counties."""
    name: str           # "Dallas-Fort Worth"
    short_name: str     # "DFW"
    fips_codes: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    owner: str = ""     # Person to flag in Slack action items
    zip_code: str = ""  # Representative zip for Visual Crossing

REMI_MARKETS: list[Market] = [
    Market("Dallas-Fort Worth", "DFW",
           ["48439", "48113", "48085", "48121", "48139", "48251",
            "48257", "48367", "48397", "48231", "48497", "48221"],
           ["TX"], "Bryan", "75201"),
    Market("Houston", "HOU",
           ["48201", "48157", "48339", "48039", "48167", "48291",
            "48071", "48473", "48015"],
           ["TX"], "Bryan", "77001"),
    Market("Oklahoma City", "OKC",
           ["40109", "40027", "40017", "40051", "40083", "40087", "40081"],
           ["OK"], "Bryan", "73101"),
    Market("Denver", "DEN",
           ["08031", "08005", "08059", "08001", "08035", "08014", "08013"],
           ["CO"], "", "80201"),
    Market("Nashville", "NSH",
           ["47037", "47187", "47149", "47165", "47189", "47147", "47021"],
           ["TN"], "", "37201"),
    Market("San Antonio", "SAT",
           ["48029", "48091", "48187", "48325", "48259"],
           ["TX"], "", "78201"),
    Market("Minneapolis", "MSP",
           ["27053", "27123", "27037", "27003", "27163", "27139", "27019"],
           ["MN"], "", "55401"),
    Market("Atlanta", "ATL",
           ["13121", "13089", "13135", "13067", "13063", "13057",
            "13117", "13151", "13097", "13223"],
           ["GA"], "", "30301"),
    Market("Phoenix", "PHX",
           ["04013", "04021"],
           ["AZ"], "", "85001"),
    Market("Raleigh", "RAL",
           ["37183", "37063", "37101", "37135", "37037"],
           ["NC"], "", "27601"),
]

# ---------------------------------------------------------------------------
# Demand window parameters
# ---------------------------------------------------------------------------

DEMAND_WINDOW_START_DAYS = 14  # Days after storm before demand rises
DEMAND_WINDOW_END_DAYS = 28    # Days after storm when demand peaks end

# ---------------------------------------------------------------------------
# Briefing parameters
# ---------------------------------------------------------------------------

BRIEFING_CATEGORICAL_MIN = 3   # SLIGHT — lower threshold for briefing
BRIEFING_MAX_DAY = 5           # Only include Days 1-5 in briefings
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_MAX_TOKENS = 1024

# ---------------------------------------------------------------------------
# NWS API constants
# ---------------------------------------------------------------------------

NWS_BASE_URL = "https://api.weather.gov"
NWS_USER_AGENT = "(remi-cat-tracker, contact@remirc.com)"
NWS_RELEVANT_EVENTS: set[str] = {
    "Tornado Warning", "Tornado Watch",
    "Severe Thunderstorm Warning", "Severe Thunderstorm Watch",
    "Hurricane Warning", "Hurricane Watch",
    "Extreme Wind Warning",
}

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
