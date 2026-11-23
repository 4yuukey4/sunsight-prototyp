"""Global configuration for SunSight.

Everything tunable about the analysis lives here so the precompute tool and
the API agree on the same parameters.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TILES_DIR = DATA_DIR / "tiles"
REGIONS_DIR = DATA_DIR / "regions"
CACHE_DIR = DATA_DIR / "cache"
VIEWS_DIR = ROOT / "views"

# --- tiling ---------------------------------------------------------------
# All analysis happens on a global XYZ web-map grid, so a city, a country or
# the world is just a different set of tiles. z14 is ~1.3 km wide in Sweden.
TILE_Z = 14
SOURCE_Z = 12          # OSM is downloaded (and cached) in coarser z12 chunks
TILE_VERSION = 2       # bump to invalidate processed tiles after a method change

# --- geometry -------------------------------------------------------------
CELL_SIZE = 2.0            # m, roof grid spacing and facade cell width
FACADE_CELL_HEIGHT = 3.0   # m, ~one storey per facade cell row
LEVEL_HEIGHT = 3.0         # m per building:levels when no height tag
DSM_RES = 1.0              # m, raster resolution for the surface model
CONTEXT_BUFFER_M = 300     # buildings this far outside a tile still cast shadows
MIN_ROOF_CELL_FRAC = 0.02  # keep clipped roof cells down to this share of a full cell

# --- horizon / ray marching ----------------------------------------------
AZ_BINS = 72               # 5 degree azimuth sectors
EL_BIN_DEG = 0.5           # sun-path histogram elevation resolution
MAX_RAY_M = 500.0          # how far horizons and views are traced
FACADE_OFFSET_M = 0.8      # ray origin offset from a wall (avoid self-hits)
ROOF_OFFSET_M = 0.2

# --- sun / climate --------------------------------------------------------
SUN_STEP_MIN = 10          # annual sun path sampling step
SUN_YEAR = 2025            # reference (non-leap) year for the sun path
CLIMATE_YEARS = (2015, 2024)
WINTER_MONTHS = (10, 11, 0, 1)  # Nov-Feb, zero-based

# --- view -----------------------------------------------------------------
VIEW_FRONT_HALF_ANGLE = 75.0    # degrees either side of the facade normal
VIEW_FEATURE_MIN_M = 15.0       # a sector "sees" green/water if >= this much is visible
VIEW_WEIGHTS = {"open": 0.6, "green": 0.2, "water": 0.2}

# --- external services (only the precompute tool talks to these) -----------
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "SunSight-prototype/0.2 (Linnaeus University research prototype)"

# Region presets (west, south, east, north). A region is the set of z14 tiles
# covering its bbox; tiles are shared between overlapping regions.
REGION_PRESETS = {
    "vaxjo": {"name": "Växjö", "bbox": (14.735, 56.845, 14.875, 56.905)},
    "vaxjo-centrum": {"name": "Växjö centrum", "bbox": (14.800, 56.874, 14.815, 56.882)},
    "stockholm-innerstad": {"name": "Stockholm innerstad", "bbox": (17.990, 59.300, 18.110, 59.350)},
    "goteborg": {"name": "Göteborg centrum", "bbox": (11.930, 57.680, 12.010, 57.720)},
    "malmo": {"name": "Malmö centrum", "bbox": (12.960, 55.580, 13.040, 55.620)},
    "kiruna": {"name": "Kiruna", "bbox": (20.180, 67.830, 20.320, 67.870)},
}
