"""Read side: processed tiles and region aggregates, shaped for the API."""
import base64
import json
import math
from functools import lru_cache

import numpy as np

from config import AZ_BINS, REGIONS_DIR
from models import horizon, raster
from models.buildings import KIND_FACADE, KIND_ROOF
from models.geo import LocalFrame
from models.tile_pipeline import CELL_METRICS, ray_origins
from models.tiles import parse_key, tile_dir

MONTHS = 12


class Tile:
    def __init__(self, key: str):
        self.key = key
        self.z, self.x, self.y = parse_key(key)
        self.dir = tile_dir(self.z, self.x, self.y)
        self.meta = json.loads((self.dir / "tile.json").read_text(encoding="utf-8"))
        self.frame = LocalFrame.from_dict(self.meta["frame"])
        self._buildings = self._cells = self._raster = self._landcover = None

    @property
    def buildings(self):
        if self._buildings is None:
            self._buildings = json.loads((self.dir / "buildings.json").read_text(encoding="utf-8"))
        return self._buildings

    @property
    def landcover(self):
        if self._landcover is None:
            self._landcover = json.loads((self.dir / "landcover.json").read_text(encoding="utf-8"))
        return self._landcover

    @property
    def cells(self):
        if self._cells is None:
            z = np.load(self.dir / "cells.npz")
            self._cells = {k: z[k] for k in z.files}
        return self._cells

    @property
    def raster(self):
        if self._raster is None:
            z = np.load(self.dir / "raster.npz")
            x0, y0, res, nx, ny = z["grid"]
            grid = raster.Grid(float(x0), float(y0), float(res), int(nx), int(ny))
            self._raster = (raster.padded(z["dsm_dm"].astype(np.float32) / 10.0), raster.padded(z["lc"]), grid)
        return self._raster

    def cell_of_slot(self, slot: int) -> int:
        sc = self.cells["slot_cell"]
        return int(sc[slot]) if 0 <= slot < len(sc) else -1

    def cell_info(self, ci: int) -> dict:
        c = self.cells
        val = {k: _f(c[f"m_{k}"][ci]) for k in CELL_METRICS}
        return {
            "tile": self.key, "cell": ci, "slot": int(c["slot"][ci]), "bid": int(c["bid"][ci]),
            "kind": "roof" if c["kind"][ci] == KIND_ROOF else "facade",
            "naz": round(float(c["naz"][ci]), 1),
            "center": np.round(c["center"][ci], 2).tolist(),
            "metrics": val,
            "sun_h_month": np.round(c["sun_h_month"][ci], 1).tolist(),
            "horizon": (c["horizon"][ci] / 2.0).round(1).tolist(),
        }

    def rays(self, ci: int) -> dict:
        dsm_pad, lc_pad, grid = self.raster
        c = self.cells
        sub = {k: c[k][ci:ci + 1] for k in ("center", "naz", "kind")}
        o = ray_origins(sub)[0]
        info = self.cell_info(ci)
        return {**info, "origin": np.round(o, 2).tolist(),
                "rays": horizon.ray_profile(float(o[0]), float(o[1]), float(o[2]), dsm_pad, lc_pad, grid)}

    def representative_view_cell(self, bid: int) -> int:
        """Facade cell whose view score is closest to the building's median (upper floors win ties)."""
        c = self.cells
        idx = np.flatnonzero((c["bid"] == bid) & (c["kind"] == KIND_FACADE))
        if not len(idx):
            idx = np.flatnonzero(c["bid"] == bid)
            if not len(idx):
                return -1
        v = c["m_view_score"][idx]
        score = np.abs(v - np.median(v)) - 1e-4 * c["center"][idx, 2]
        return int(idx[np.argmin(score)])


@lru_cache(maxsize=512)
def tile(key: str) -> Tile:
    if not (tile_dir(*parse_key(key)) / "tile.json").exists():
        raise KeyError(key)
    return Tile(key)


class Region:
    def __init__(self, rid: str):
        d = REGIONS_DIR / rid
        self.meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.geojson = (d / "buildings.geojson").read_text(encoding="utf-8")
        self.props = {f["properties"]["id"]: f["properties"] for f in json.loads(self.geojson)["features"]}


@lru_cache(maxsize=32)
def region(rid: str) -> Region:
    if not (REGIONS_DIR / rid / "meta.json").exists():
        raise KeyError(rid)
    return Region(rid)


def list_regions():
    out = []
    for p in sorted(REGIONS_DIR.glob("*/meta.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        out.append({k: m[k] for k in ("id", "name", "bbox", "counts", "summary", "timezone")})
    return out


def building_detail(key: str, bid: int, rid: str | None) -> dict:
    t = tile(key)
    b = t.buildings[bid]
    p = {k: b[k] for k in ("bid", "osm_id", "core", "h", "height_source", "type", "name", "address")}
    p.update({"tile": key, "id": f"{key}:{bid}", **(b.get("m") or {}),
              "sun_h_month": b.get("sun_h_month"), "facade_area": b.get("facade_area"), "roof_area": b.get("roof_area")})
    if rid:
        try:
            rp = region(rid).props.get(p["id"], {})
            p.update({k: rp.get(k) for k in ("pct_sun_h", "pct_view_score", "pct_winter_sun_h", "overall")})
        except KeyError:
            pass
    c = t.cells
    m = c["bid"] == bid
    p["n_roof_cells"] = int((m & (c["kind"] == KIND_ROOF)).sum())
    p["n_facade_cells"] = int((m & (c["kind"] == KIND_FACADE)).sum())
    fac = m & (c["kind"] == KIND_FACADE)
    if fac.any():
        p["facade_sun_h_range"] = [round(float(c["m_sun_h"][fac].min())), round(float(c["m_sun_h"][fac].max()))]
    p["view_cell"] = t.representative_view_cell(bid)
    return p


def scene(key: str, bid: int, ctx_radius: float = 300.0, cell_radius: float = 90.0) -> dict:
    """Everything the 3D inspector needs around one building, in that building's frame.

    Coordinates are metres relative to the target building's centroid. Tile
    meshes are placed by translation (`tiles[].offset`); the residual scale
    difference between neighbouring tile frames is < 0.1 m within 300 m.
    """
    t = tile(key)
    ft = t.frame
    cx, cy = t.buildings[bid]["c"]
    near = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            try:
                near.append(tile(f"{t.z}-{t.x + dx}-{t.y + dy}"))
            except KeyError:
                pass
    tiles_out, blds, seen, lcov, hz = [], [], set(), [], {}
    for nt in near:
        ox, oy = ft.to_xy(nt.frame.lon0, nt.frame.lat0)
        off = np.array([float(ox) - cx, float(oy) - cy])
        tiles_out.append({"key": nt.key, "offset": np.round(off, 3).tolist()})
        for b in nt.buildings:
            c = np.asarray(b["c"]) + off
            d = float(np.hypot(*c))
            if d > ctx_radius:
                continue
            sig = (b["osm_id"], round(c[0]), round(c[1]))
            if sig in seen:
                continue
            seen.add(sig)
            blds.append({"h": b["h"], "surfaced": bool(b["core"]), "tile": nt.key, "bid": b["bid"],
                         "rings": [(np.asarray(r) + off).round(2).tolist() for r in b["rings"]]})
        for f in nt.landcover:
            r0 = np.asarray(f["rings"][0]) + off
            if np.min(np.hypot(r0[:, 0], r0[:, 1])) > ctx_radius + 200:
                continue
            lcov.append({"cls": f["cls"], "rings": [(np.asarray(r) + off).round(1).tolist() for r in f["rings"]]})
        if nt.meta["counts"]["cells"]:
            c = nt.cells
            bids = [b["bid"] for b in nt.buildings
                    if b["core"] and np.hypot(*(np.asarray(b["c"]) + off)) <= cell_radius]
            m = np.isin(c["bid"], bids)
            if m.any():
                hz[nt.key] = {"slots": c["slot"][m].tolist(), "kind": c["kind"][m].tolist(),
                              "naz": np.round(c["naz"][m], 1).tolist(),
                              "horizon_b64": base64.b64encode(np.ascontiguousarray(c["horizon"][m]).tobytes()).decode()}
    tb = t.buildings[bid]
    return {"target": {"tile": key, "bid": bid, "h": tb["h"], "name": tb["name"], "address": tb["address"]},
            "tiles": tiles_out, "buildings": blds, "landcover": lcov, "horizons": hz,
            "az_bins": AZ_BINS, "timezone": t.meta["timezone"], "lat": ft.lat0, "lon": ft.lon0}


def _f(v):
    v = float(v)
    return None if math.isnan(v) else round(v, 4)
