"""OpenStreetMap acquisition via Overpass: buildings, water and green areas."""
import json
import pickle
import time
from functools import lru_cache
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

from config import OVERPASS_URLS, USER_AGENT

_GREEN_LANDUSE = "grass|forest|meadow|recreation_ground|village_green|cemetery|allotments"
_GREEN_LEISURE = "park|garden|nature_reserve|golf_course|playground"
_GREEN_NATURAL = "wood|grassland|scrub|heath|wetland"

_QUERY = """
[out:json][timeout:180];
(
  way["building"]({b});
  relation["building"]["type"="multipolygon"]({b});
  way["natural"="water"]({b});
  relation["natural"="water"]({b});
  way["waterway"="riverbank"]({b});
  way["landuse"~"^(reservoir|basin)$"]({b});
  way["landuse"~"^({gl})$"]({b});
  relation["landuse"~"^({gl})$"]({b});
  way["leisure"~"^({gs})$"]({b});
  relation["leisure"~"^({gs})$"]({b});
  way["natural"~"^({gn})$"]({b});
  relation["natural"~"^({gn})$"]({b});
);
out geom;
"""


def fetch_overpass(bbox, cache_path: Path | None = None) -> dict:
    """bbox = (west, south, east, north) in degrees."""
    if cache_path and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    w, s, e, n = bbox
    q = _QUERY.format(b=f"{s},{w},{n},{e}", gl=_GREEN_LANDUSE, gs=_GREEN_LEISURE, gn=_GREEN_NATURAL)
    data = urllib.parse.urlencode({"data": q}).encode()
    raw, errors = None, []
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=240) as r:
                    raw = json.loads(r.read().decode("utf-8"))
                break
            except Exception as exc:  # mirrors are flaky; try the next one
                errors.append(f"{url}: {exc}")
        if raw is not None:
            break
        time.sleep(10 * (attempt + 1))
    if raw is None:
        raise RuntimeError("All Overpass endpoints failed:\n" + "\n".join(errors))
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw), encoding="utf-8")
    return raw


def classify(tags: dict) -> str | None:
    if tags.get("building", "no") != "no":
        return "building"
    if (tags.get("natural") == "water" or tags.get("waterway") == "riverbank"
            or tags.get("landuse") in ("reservoir", "basin")):
        return "water"
    if (tags.get("landuse") in _GREEN_LANDUSE.split("|")
            or tags.get("leisure") in _GREEN_LEISURE.split("|")
            or tags.get("natural") in _GREEN_NATURAL.split("|")):
        return "green"
    return None


def _ring(geom_list):
    return [(p["lon"], p["lat"]) for p in geom_list]


def _way_polygon(el):
    pts = _ring(el.get("geometry", []))
    if len(pts) < 4 or pts[0] != pts[-1]:
        return None
    p = Polygon(pts)
    return p if p.is_valid else p.buffer(0)


def _relation_polygon(el):
    outer, inner = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        line = LineString(_ring(m["geometry"]))
        (inner if m.get("role") == "inner" else outer).append(line)
    if not outer:
        return None
    shell = unary_union(list(polygonize(unary_union(outer))))
    if shell.is_empty:
        return None
    if inner:
        holes = unary_union(list(polygonize(unary_union(inner))))
        shell = shell.difference(holes)
    return shell


def parse_features(raw: dict) -> dict:
    """Return {"building": [...], "water": [...], "green": [...]} of lon/lat geometries."""
    out = {"building": [], "water": [], "green": []}
    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        kind = classify(tags)
        if kind is None:
            continue
        geom = _way_polygon(el) if el["type"] == "way" else _relation_polygon(el)
        if geom is None or geom.is_empty:
            continue
        out[kind].append({"osm_id": f"{el['type'][0]}{el['id']}", "tags": tags, "geom": geom})
    return out


# --- chunked, cached access ---------------------------------------------------
# OSM is downloaded once per z12 chunk (~5 km in Sweden) and kept as parsed
# geometry, so processing a z14 tile never talks to Overpass directly and a
# country-scale run can later swap in a Geofabrik extract behind the same API.

_KEEP_TAGS = ("building", "height", "building:height", "building:levels", "roof:levels",
              "name", "addr:street", "addr:housenumber", "natural", "landuse", "leisure", "waterway")


def chunk_path(x: int, y: int) -> Path:
    from config import CACHE_DIR, SOURCE_Z
    return CACHE_DIR / "osm" / str(SOURCE_Z) / f"{x}_{y}.pkl"


def ensure_chunk(x: int, y: int) -> Path:
    from config import SOURCE_Z
    from models.tiles import tile_bounds
    p = chunk_path(x, y)
    if p.exists():
        return p
    feats = _fetch_split(tile_bounds(SOURCE_Z, x, y), depth=0)
    for group in feats.values():
        for f in group:
            f["tags"] = {k: v for k, v in f["tags"].items() if k in _KEEP_TAGS}
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        pickle.dump(feats, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return p


def _fetch_split(bbox, depth: int) -> dict:
    """Fetch a bbox; if Overpass refuses (load, timeouts), split into quadrants."""
    try:
        return parse_features(fetch_overpass(bbox))
    except RuntimeError:
        if depth >= 2:
            raise
    w, s, e, n = bbox
    mx, my = (w + e) / 2, (s + n) / 2
    merged, seen = {"building": [], "water": [], "green": []}, set()
    for q in ((w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)):
        time.sleep(2)
        for kind, group in _fetch_split(q, depth + 1).items():
            for f in group:
                if f["osm_id"] not in seen:
                    seen.add(f["osm_id"])
                    merged[kind].append(f)
    return merged


@lru_cache(maxsize=8)
def _load_chunk(x: int, y: int) -> dict:
    with open(ensure_chunk(x, y), "rb") as fh:
        return pickle.load(fh)


def chunks_for_bbox(bbox):
    from config import SOURCE_Z
    from models.tiles import tiles_covering
    return [(x, y) for _, x, y in tiles_covering(bbox, SOURCE_Z)]


def features_in_bbox(bbox) -> dict:
    """All buildings / water / green intersecting bbox, deduplicated across chunks."""
    w, s, e, n = bbox
    out = {"building": [], "water": [], "green": []}
    seen = set()
    for cx, cy in chunks_for_bbox(bbox):
        for kind, group in _load_chunk(cx, cy).items():
            for f in group:
                if f["osm_id"] in seen:
                    continue
                gx0, gy0, gx1, gy1 = f["geom"].bounds
                if gx1 < w or gx0 > e or gy1 < s or gy0 > n:
                    continue
                seen.add(f["osm_id"])
                out[kind].append(f)
    return out
