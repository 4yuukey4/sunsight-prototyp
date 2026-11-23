"""Buildings (footprint + height) and their surface cells.

Every analysed building is split into cells, the "squares" the UI colours:
  * roof cells: a CELL_SIZE grid aligned to the building's dominant axis,
    clipped to the footprint (roofs are treated as flat for now);
  * facade cells: each wall edge cut into ~CELL_SIZE wide strips and
    ~one-storey rows, with an outward normal.
"""
import math
import re
from dataclasses import dataclass

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient

from config import CELL_SIZE, FACADE_CELL_HEIGHT, LEVEL_HEIGHT, MIN_ROOF_CELL_FRAC

KIND_ROOF, KIND_FACADE = 0, 1

# Fallback heights (m) by building=* value when OSM has neither height nor levels.
DEFAULT_HEIGHTS = {
    "house": 6.5, "detached": 6.5, "semidetached_house": 6.5, "terrace": 7.0, "bungalow": 4.5,
    "cabin": 4.0, "farm": 6.5, "residential": 9.0, "apartments": 12.0, "dormitory": 12.0,
    "garage": 3.0, "garages": 3.0, "carport": 2.5, "shed": 2.5, "hut": 3.0, "roof": 4.0,
    "greenhouse": 3.5, "kiosk": 3.0, "service": 3.5, "toilets": 3.0, "container": 2.6,
    "commercial": 10.0, "retail": 7.0, "office": 15.0, "industrial": 8.0, "warehouse": 8.0,
    "school": 10.0, "university": 14.0, "hospital": 18.0, "church": 15.0, "cathedral": 25.0,
    "hotel": 15.0, "public": 10.0, "civic": 10.0, "train_station": 9.0, "parking": 9.0,
}


def _num(v):
    if v is None:
        return None
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v))
    if not m:
        return None
    x = float(m.group(0).replace(",", "."))
    if "ft" in str(v) or "'" in str(v):
        x *= 0.3048
    return x


def estimate_height(tags: dict):
    """Return (height_m, source) where source is 'height', 'levels' or 'default'."""
    for k in ("height", "building:height"):
        h = _num(tags.get(k))
        if h and 1.0 <= h <= 700.0:
            return h, "height"
    lv = _num(tags.get("building:levels"))
    if lv is not None and 0 < lv < 200:
        roof = _num(tags.get("roof:levels")) or 0.0
        return lv * LEVEL_HEIGHT + roof * LEVEL_HEIGHT * 0.5 + 0.5, "levels"
    return DEFAULT_HEIGHTS.get(tags.get("building", "yes"), 9.0), "default"


@dataclass
class Building:
    bid: int
    osm_id: str
    poly: Polygon          # local metric frame, oriented (shell CCW)
    height: float
    height_source: str
    btype: str
    name: str | None
    address: str | None
    core: bool             # inside the analysed area (others only cast shadows)


def make_buildings(features, frame, core_poly) -> list[Building]:
    out = []
    for f in features:
        tags = f["tags"]
        h, src = estimate_height(tags)
        geom = frame.geom_to_xy(f["geom"])
        parts = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        street, num = tags.get("addr:street"), tags.get("addr:housenumber")
        addr = f"{street} {num}" if street and num else street
        for p in parts:
            if not isinstance(p, Polygon):
                continue
            if not p.is_valid:
                p = p.buffer(0)
                if not isinstance(p, Polygon):
                    continue
            p = p.simplify(0.05)
            if p.area < 4.0:
                continue
            out.append(Building(
                bid=len(out), osm_id=f["osm_id"], poly=orient(p, 1.0), height=float(h),
                height_source=src, btype=tags.get("building", "yes"), name=tags.get("name"),
                address=addr, core=bool(core_poly.contains(p.centroid)),
            ))
    return out


def roof_grid(b: Building, cell: float):
    """Roof grid aligned to the footprint's dominant axis.

    Returns (ang_rad, cx, cy, ox, oy, nx, ny): rotate a point by -ang about
    (cx, cy), subtract (ox, oy) and divide by `cell` to get grid coordinates.
    """
    poly = b.poly
    rect = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)
    edges = np.diff(rect[:3], axis=0)
    e = edges[np.argmax(np.hypot(edges[:, 0], edges[:, 1]))]
    ang = math.atan2(e[1], e[0])
    c = poly.centroid
    pr = affinity.rotate(poly, -ang, origin=c, use_radians=True)
    minx, miny, maxx, maxy = pr.bounds
    nx = max(1, math.ceil((maxx - minx) / cell))
    ny = max(1, math.ceil((maxy - miny) / cell))
    ox = minx - (nx * cell - (maxx - minx)) / 2
    oy = miny - (ny * cell - (maxy - miny)) / 2
    return ang, c.x, c.y, ox, oy, nx, ny, pr


def _roof_cells(b: Building, cell: float):
    ang, cx, cy, ox, oy, nx, ny, pr = roof_grid(b, cell)
    X, Y = np.meshgrid(ox + np.arange(nx) * cell, oy + np.arange(ny) * cell)
    X, Y = X.ravel(), Y.ravel()
    inter = shapely.intersection(shapely.box(X, Y, X + cell, Y + cell), pr)
    areas = shapely.area(inter)
    cells = []
    for slot, (g, a) in enumerate(zip(inter, areas)):
        if a < MIN_ROOF_CELL_FRAC * cell * cell:
            continue
        if g.geom_type != "Polygon":
            polys = [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]
            if not polys:
                continue
            g = max(polys, key=lambda q: q.area)
        g = affinity.rotate(g, ang, origin=(cx, cy), use_radians=True)
        ctr = g.centroid if g.contains(g.centroid) else g.representative_point()
        cells.append((slot, (ctr.x, ctr.y), float(a)))
    return (ang, cx, cy, ox, oy, nx, ny), cells


def make_cells(buildings: list[Building], cell: float = CELL_SIZE, fh: float = FACADE_CELL_HEIGHT):
    """Cut core buildings into cells and describe the surfaces they tile.

    Every surface owns a contiguous block of "slots" (its cell matrix, row
    major). Cells reference their slot; slots without a cell (party walls,
    roof grid squares outside the footprint) stay empty. The renderer draws
    one quad per wall / one polygon per roof and looks values up by slot.
    """
    bid, kind, center, naz, area, slot = [], [], [], [], [], []
    walls, roofs, slot_kind = [], [], []
    for b in buildings:
        if not b.core:
            continue
        h = b.height
        (ang, cx, cy, ox, oy, nx, ny), rcells = _roof_cells(b, cell)
        base = len(slot_kind)
        slot_kind.extend([KIND_ROOF] * (nx * ny))
        roofs.append({"bid": b.bid, "h": h, "ang": ang, "cx": cx, "cy": cy, "ox": ox, "oy": oy,
                      "nx": nx, "ny": ny, "base": base,
                      "rings": [np.asarray(r.coords)[:-1] for r in [b.poly.exterior, *b.poly.interiors]]})
        for s_, (px, py), a in rcells:
            bid.append(b.bid); kind.append(KIND_ROOF); center.append((px, py, h)); naz.append(0.0)
            area.append(a); slot.append(base + s_)
        nz = max(1, int(round(h / fh)))
        zs = np.linspace(0.0, h, nz + 1)
        for ring in [b.poly.exterior, *b.poly.interiors]:
            pts = np.asarray(ring.coords)
            for p0, p1 in zip(pts[:-1], pts[1:]):
                d = p1 - p0
                L = float(np.hypot(*d))
                if L < 0.5:
                    continue
                nrm = np.array([d[1], -d[0]]) / L        # outward: right of travel
                az = math.degrees(math.atan2(nrm[0], nrm[1])) % 360.0
                nseg = max(1, int(round(L / cell)))
                base = len(slot_kind)
                slot_kind.extend([KIND_FACADE] * (nseg * nz))
                walls.append((b.bid, p0[0], p0[1], p1[0], p1[1], 0.0, h, nseg, nz, base, az))
                for k in range(nseg):
                    mid = p0 + d * (k + 0.5) / nseg
                    for j in range(nz):
                        bid.append(b.bid); kind.append(KIND_FACADE)
                        center.append((mid[0], mid[1], (zs[j] + zs[j + 1]) / 2)); naz.append(az)
                        area.append(L / nseg * (zs[j + 1] - zs[j])); slot.append(base + j * nseg + k)
    cells = {
        "bid": np.asarray(bid, dtype=np.int32),
        "kind": np.asarray(kind, dtype=np.uint8),
        "center": np.asarray(center, dtype=np.float64).reshape(-1, 3),
        "naz": np.asarray(naz, dtype=np.float32),
        "area": np.asarray(area, dtype=np.float32),
        "slot": np.asarray(slot, dtype=np.int32),
    }
    surfaces = {"walls": np.asarray(walls, dtype=np.float64).reshape(-1, 11), "roofs": roofs,
                "slot_kind": np.asarray(slot_kind, dtype=np.uint8)}
    return cells, surfaces


def subset_cells(cells: dict, mask) -> dict:
    return {k: v[mask] for k, v in cells.items()}


def cell_polygon(cells: dict, i: int, roofs_by_bid: dict, cell: float = CELL_SIZE):
    """Reconstruct the 3D polygon of one cell (used by the 3D inspector)."""
    if cells["kind"][i] == KIND_ROOF:
        r = roofs_by_bid[int(cells["bid"][i])]
        s_ = int(cells["slot"][i]) - r["base"]
        gx, gy = s_ % r["nx"], s_ // r["nx"]
        x0, y0 = r["ox"] + gx * cell, r["oy"] + gy * cell
        sq = shapely.box(x0, y0, x0 + cell, y0 + cell)
        poly = Polygon(np.vstack(r["rings"][0]), [np.vstack(h) for h in r["rings"][1:]])
        poly = affinity.rotate(poly, -r["ang"], origin=(r["cx"], r["cy"]), use_radians=True)
        g = sq.intersection(poly)
        if g.geom_type != "Polygon":
            polys = [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]
            g = max(polys, key=lambda q: q.area) if polys else sq
        g = affinity.rotate(g, r["ang"], origin=(r["cx"], r["cy"]), use_radians=True)
        xy = np.asarray(g.exterior.coords)[:-1]
        return np.column_stack([xy, np.full(len(xy), r["h"])])
    return None  # facade cells are rebuilt from their wall (see store.scene)
