"""Configuration constants and paths."""
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_ROOT / "cache"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = PROJECT_ROOT / "data" / "forecaster.db"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── External data URLs ────────────────────────────────────────────────
IBTRACS_NI_CSV_URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs"
    "/v04r01/access/csv/ibtracs.NI.list.v04r01.csv"
)
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

# ── GEE dataset IDs (all verified against GEE catalog) ───────────────
GEE_S1_GRD = "COPERNICUS/S1_GRD"
GEE_DEM_GLO30 = "COPERNICUS/DEM/GLO30"           # band: 'DEM'
GEE_JRC_GSW = "JRC/GSW1_4/GlobalSurfaceWater"     # band: 'occurrence'
GEE_GHSL_POP = "JRC/GHSL/P2023A/GHS_POP"          # band: 'population_count'

# ── Study area (West Bengal coastal districts) ────────────────────────
# Bounding box: approx SW corner to NE corner
STUDY_BBOX = {
    "west": 87.5,
    "south": 21.5,
    "east": 89.1,
    "north": 23.0,
}

# ── Events ────────────────────────────────────────────────────────────
EVENTS = {
    "amphan": {
        "name": "Cyclone Amphan",
        "ibtracs_sid": "2020121N10087",
        "landfall_utc": "2020-05-20T10:00:00Z",
        "cutoff_utc": "2020-05-20T10:00:00Z",
        "study_bbox": STUDY_BBOX,
    },
    "remal": {
        "name": "Cyclone Remal",
        "ibtracs_sid": "2024143N14088",
        "landfall_utc": "2024-05-26T18:00:00Z",
        "cutoff_utc": "2024-05-26T18:00:00Z",
        "study_bbox": {
            "west": 88.0,
            "south": 21.0,
            "east": 90.0,
            "north": 23.5,
        },
    },
}

# ── Hazard model parameters ──────────────────────────────────────────
FLOOD_THRESHOLD_RATIO = 1.25        # S1 VV ratio threshold (UN-SPIDER)
SURGE_BASELINE_ELEVATION_M = 5.0    # Naive baseline: coast cells below N m
HAZARD_WEIGHTS = {
    "elevation": 0.25,
    "coastal_distance": 0.20,
    "hydro_connectivity": 0.15,
    "surge_proxy": 0.25,
    "rainfall": 0.15,
}

# ── Gemini ────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_PRIMARY = "gemini-3.7-flash"
GEMINI_MODEL_FALLBACK = "gemini-3.8-flash"
ADVISORY_MAX_RETRIES = 2

# ── Overpass (OSM) ────────────────────────────────────────────────────
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_ASSET_TYPES = ["hospital", "road_primary", "road_trunk", "power_substation",
                   "power_line", "shelter"]
